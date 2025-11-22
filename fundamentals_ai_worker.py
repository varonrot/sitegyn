import os
import json
import time
from datetime import datetime, date

import requests
from supabase import create_client
from openai import OpenAI
from dotenv import load_dotenv

# ==========================
# Load environment
# ==========================
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
FMP_API_KEY = os.getenv("FMP_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")

if not FMP_API_KEY:
    raise RuntimeError("Missing FMP_API_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

FMP_BASE = "https://financialmodelingprep.com/api/v3"

PROMPTER_NAME = "fundamentals_ai_analyst_v1"
PROMPT_FILE = "fundamentals_ai_analyst_v1_prompt.txt"

# ==========================
# Helpers
# ==========================

def load_system_prompt() -> str:
    """Load system prompt from txt file."""
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        return f.read()


def get_symbols_from_earnings_calendar() -> list[str]:
    """
    שליפת רשימת סימבולים מטבלת earnings_calendar_us.
    מניח שהסינון (ללא נקודה, עד 4 תווים וכו') כבר נעשה לפני.
    """
    resp = supabase.table("earnings_calendar_us").select("symbol", distinct=True).execute()
    symbols = []
    for row in resp.data or []:
        sym = (row.get("symbol") or "").strip().upper()
        if sym:
            symbols.append(sym)
    # הסרה של כפילויות ליתר ביטחון
    return sorted(list(set(symbols)))


def fmp_get(path: str, params: dict | None = None) -> list[dict]:
    """Call FMP endpoint and return list of dicts (or [] on error)."""
    if params is None:
        params = {}
    params["apikey"] = FMP_API_KEY
    url = f"{FMP_BASE}/{path}"
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            # חלק מה־API של FMP מחזירים dict במקום list
            return [data]
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        print(f"[FMP ERROR] {path}: {e}")
        return []


# ==========================
# Fetch data for single symbol
# ==========================

def fetch_company_profile(symbol: str) -> dict | None:
    data = fmp_get(f"profile/{symbol}")
    return data[0] if data else None


def fetch_income_quarters(symbol: str) -> list[dict]:
    return fmp_get(f"income-statement/{symbol}", {"period": "quarter", "limit": 2})


def fetch_cashflow_quarters(symbol: str) -> list[dict]:
    return fmp_get(f"cash-flow-statement/{symbol}", {"period": "quarter", "limit": 2})


def fetch_balancesheet_quarters(symbol: str) -> list[dict]:
    return fmp_get(f"balance-sheet-statement/{symbol}", {"period": "quarter", "limit": 2})


def fetch_analyst_estimates(symbol: str) -> list[dict]:
    return fmp_get(f"analyst-estimates/{symbol}")


def fetch_price_target(symbol: str) -> dict | None:
    data = fmp_get(f"price-target/{symbol}")
    return data[0] if data else None


def fetch_analyst_consensus(symbol: str) -> dict | None:
    # אם אין לך את ה־endpoint הזה בחבילה, אפשר לוותר ולהחזיר None
    data = fmp_get(f"analyst-consensus/{symbol}")
    return data[0] if data else None


def fetch_news(symbol: str, limit: int = 5) -> list[dict]:
    data = fmp_get("stock_news", {"tickers": symbol, "limit": limit})
    news_items = []
    for item in data:
        news_items.append({
            "headline": item.get("title") or item.get("headline") or "",
            "published_date": item.get("publishedDate") or item.get("date") or "",
            "source": item.get("site") or item.get("source") or ""
        })
    return news_items


# ==========================
# Transform FMP data into payload JSON
# ==========================

def align_quarters(income: list[dict], cashflow: list[dict], balance: list[dict]) -> tuple[dict, dict] | None:
    """
    מיישר שני רבעונים: latest ו-previous לכל שלושת הדוחות.
    מניח שה־list כבר ממויין מהחדש לישן לפי FMP.
    אם אין מספיק נתונים – מחזיר None.
    """
    if len(income) < 2 or len(cashflow) < 2 or len(balance) < 2:
        return None

    # כאן אני מניח שהתאריכים תואמים לפי אינדקס
    # אם תרצה הקשחה – אפשר לעשות matching לפי date.
    latest = {
        "income": income[0],
        "cashflow": cashflow[0],
        "balance": balance[0],
    }
    previous = {
        "income": income[1],
        "cashflow": cashflow[1],
        "balance": balance[1],
    }
    return latest, previous


def safe_div(num, den):
    try:
        if num is None or den in (None, 0):
            return None
        return float(num) / float(den)
    except Exception:
        return None


def compute_quarter_block(inc: dict, cf: dict, bs: dict) -> dict:
    """
    בונה בלוק של latest_quarter / previous_quarter מתוך 3 הדוחות.
    חשוב: ייתכן שחלק מהשדות יחזרו כ-None – זה בסדר.
    """
    revenue = inc.get("revenue")
    gross_profit = inc.get("grossProfit")
    operating_income = inc.get("operatingIncome")
    net_income = inc.get("netIncome")
    eps = inc.get("eps")
    eps_diluted = inc.get("epsdiluted") or inc.get("epsDiluted")

    operating_cash_flow = cf.get("operatingCashFlow")
    capex = cf.get("capitalExpenditure")
    free_cash_flow = cf.get("freeCashFlow")
    if free_cash_flow is None and operating_cash_flow is not None and capex is not None:
        free_cash_flow = float(operating_cash_flow) + float(capex)

    total_assets = bs.get("totalAssets")
    total_liabilities = bs.get("totalLiabilities")
    total_equity = bs.get("totalStockholdersEquity") or bs.get("totalEquity")
    cash_and_eq = bs.get("cashAndCashEquivalents") or bs.get("cashAndCashEquivalentsShortTerm") or bs.get("cashAndShortTermInvestments")
    short_term_debt = bs.get("shortTermDebt")
    long_term_debt = bs.get("longTermDebt")
    total_debt = bs.get("totalDebt")
    if total_debt is None:
        vals = [short_term_debt, long_term_debt]
        vals = [float(v) for v in vals if v is not None]
        if vals:
            total_debt = sum(vals)

    current_ratio = bs.get("currentRatio")
    quick_ratio = bs.get("quickRatio")
    # אם current_ratio/quick_ratio לא קיימים בבלאנס, אפשר לחשב מ-ratios בטווח עתידי

    debt_to_equity = safe_div(total_debt, total_equity)

    # margin percentages
    gross_margin_pct = safe_div(gross_profit, revenue)
    if gross_margin_pct is not None:
        gross_margin_pct *= 100.0

    operating_margin_pct = safe_div(operating_income, revenue)
    if operating_margin_pct is not None:
        operating_margin_pct *= 100.0

    net_margin_pct = safe_div(net_income, revenue)
    if net_margin_pct is not None:
        net_margin_pct *= 100.0

    # interest coverage – אין ברירת מחדל בדוחות הגולמיים, לרוב נשתמש ב-ratios
    # placeholder: None
    interest_coverage_ratio = bs.get("interestCoverage")  # אם בעתיד תיקח מ-ratios

    # ROE / ROA – גם לרוב מנתוני key-metrics / ratios
    return_on_equity_pct = bs.get("returnOnEquity")  # אם העתיד תשתמש ב-key-metrics
    return_on_assets_pct = bs.get("returnOnAssets")

    period_end_date = inc.get("date")  # string 'YYYY-MM-DD'
    calendar_year = inc.get("calendarYear")
    period = inc.get("period")  # 'Q1','Q2','Q3','Q4' / 'FY'

    period_label = None
    if calendar_year and period:
        period_label = f"{calendar_year}-{period}"

    return {
        "period_label": period_label,
        "period_end_date": period_end_date,
        "revenue": revenue,
        "gross_profit": gross_profit,
        "operating_income": operating_income,
        "net_income": net_income,
        "eps": eps,
        "eps_diluted": eps_diluted,
        "operating_cash_flow": operating_cash_flow,
        "capital_expenditure": capex,
        "free_cash_flow": free_cash_flow,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "cash_and_equivalents": cash_and_eq,
        "short_term_debt": short_term_debt,
        "long_term_debt": long_term_debt,
        "total_debt": total_debt,
        "current_ratio": current_ratio,
        "quick_ratio": quick_ratio,
        "debt_to_equity": debt_to_equity,
        "interest_coverage_ratio": interest_coverage_ratio,
        "gross_margin_pct": gross_margin_pct,
        "operating_margin_pct": operating_margin_pct,
        "net_margin_pct": net_margin_pct,
        "return_on_equity_pct": return_on_equity_pct,
        "return_on_assets_pct": return_on_assets_pct,
    }


def compute_derived_metrics(latest: dict, prev: dict) -> dict:
    """
    חישוב צמיחה ושינויים בין הרבעון האחרון לרבעון הקודם.
    """
    revenue_growth_qoq_pct = None
    eps_growth_qoq_pct = None
    fcf_growth_qoq_pct = None

    revenue_growth_qoq_pct = safe_div(
        (latest.get("revenue") or 0) - (prev.get("revenue") or 0),
        prev.get("revenue") or 0
    )
    if revenue_growth_qoq_pct is not None:
        revenue_growth_qoq_pct *= 100.0

    eps_growth_qoq_pct = safe_div(
        (latest.get("eps") or 0) - (prev.get("eps") or 0),
        prev.get("eps") or 0
    )
    if eps_growth_qoq_pct is not None:
        eps_growth_qoq_pct *= 100.0

    fcf_growth_qoq_pct = safe_div(
        (latest.get("free_cash_flow") or 0) - (prev.get("free_cash_flow") or 0),
        prev.get("free_cash_flow") or 0
    )
    if fcf_growth_qoq_pct is not None:
        fcf_growth_qoq_pct *= 100.0

    def delta(field):
        l = latest.get(field)
        p = prev.get(field)
        if l is None or p is None:
            return None
        try:
            return float(l) - float(p)
        except Exception:
            return None

    gross_margin_change_pts = delta("gross_margin_pct")
    operating_margin_change_pts = delta("operating_margin_pct")
    net_margin_change_pts = delta("net_margin_pct")

    debt_to_equity_change = delta("debt_to_equity")
    current_ratio_change = delta("current_ratio")
    quick_ratio_change = delta("quick_ratio")

    # TTM placeholders – אפשר לחשב בעתיד אם שולפים 4 רבעונים
    return {
        "revenue_growth_qoq_pct": revenue_growth_qoq_pct,
        "eps_growth_qoq_pct": eps_growth_qoq_pct,
        "fcf_growth_qoq_pct": fcf_growth_qoq_pct,
        "gross_margin_change_pts": gross_margin_change_pts,
        "operating_margin_change_pts": operating_margin_change_pts,
        "net_margin_change_pts": net_margin_change_pts,
        "debt_to_equity_change": debt_to_equity_change,
        "current_ratio_change": current_ratio_change,
        "quick_ratio_change": quick_ratio_change,
        "revenue_ttm": None,
        "net_income_ttm": None,
        "free_cash_flow_ttm": None,
    }


def extract_analyst_estimates_struct(estimates: list[dict]) -> dict:
    """
    מתוך analyst-estimates בוחר next_quarter ו-next_year.
    בפועל תצטרך להתאים לנתונים ש-FMP מחזירה (שמות שדות ותאריכים).
    כרגע זה skeleton שכנראה תעדכן אחרי שתראה את ה-JSON האמיתי מ-FMP.
    """
    next_quarter = {
        "period_label": "",
        "revenue_estimate": None,
        "eps_estimate": None,
    }
    next_year = {
        "fiscal_year": None,
        "revenue_estimate": None,
        "eps_estimate": None,
    }

    # TODO: להתאים ניתוח לפי השדות בפועל של FMP
    # כאן אפשר לסרוק estimates ולחפש רשומה עם type רבעוני + רשומה עם type שנתי

    return {
        "next_quarter": next_quarter,
        "next_year": next_year,
        "expected_revenue_growth_next_year_pct": None,
        "expected_eps_growth_next_year_pct": None,
    }


def extract_analyst_sentiment_struct(consensus: dict | None) -> dict:
    if not consensus:
        return {
            "analyst_rating_score": None,
            "analyst_rating_label": "",
            "analyst_buy_ratings": None,
            "analyst_hold_ratings": None,
            "analyst_sell_ratings": None,
        }

    return {
        "analyst_rating_score": consensus.get("ratingScore"),
        "analyst_rating_label": consensus.get("rating"),
        "analyst_buy_ratings": consensus.get("ratingDetails", {}).get("strongBuy") or consensus.get("buy"),
        "analyst_hold_ratings": consensus.get("ratingDetails", {}).get("hold"),
        "analyst_sell_ratings": consensus.get("ratingDetails", {}).get("sell"),
    }


def extract_price_targets_struct(price_target: dict | None, current_price: float | None) -> dict:
    if not price_target:
        return {
            "price_target_low": None,
            "price_target_average": None,
            "price_target_high": None,
            "number_of_analysts": None,
            "current_price": current_price,
        }
    return {
        "price_target_low": price_target.get("targetLow"),
        "price_target_average": price_target.get("targetMean"),
        "price_target_high": price_target.get("targetHigh"),
        "number_of_analysts": price_target.get("numberOfAnalysts"),
        "current_price": current_price,
    }


def build_payload_for_symbol(symbol: str) -> tuple[dict | None, dict | None]:
    """
    מחזיר:
      - payload_json (מה שנשלח ל-GPT)
      - meta_info (sector, industry, report_period_end וכו' לשימוש בשמירה)
    אם חסר מידע קריטי – מחזיר (None, None).
    """
    profile = fetch_company_profile(symbol)
    income_q = fetch_income_quarters(symbol)
    cash_q = fetch_cashflow_quarters(symbol)
    bs_q = fetch_balancesheet_quarters(symbol)
    estimates = fetch_analyst_estimates(symbol)
    price_target = fetch_price_target(symbol)
    consensus = fetch_analyst_consensus(symbol)
    news = fetch_news(symbol, limit=5)

    aligned = align_quarters(income_q, cash_q, bs_q)
    if not aligned:
        print(f"[WARN] Not enough quarterly data for {symbol}")
        return None, None

    latest_raw, prev_raw = aligned
    latest_block = compute_quarter_block(
        latest_raw["income"], latest_raw["cashflow"], latest_raw["balance"]
    )
    prev_block = compute_quarter_block(
        prev_raw["income"], prev_raw["cashflow"], prev_raw["balance"]
    )
    derived = compute_derived_metrics(latest_block, prev_block)

    analyst_estimates_struct = extract_analyst_estimates_struct(estimates)
    analyst_sentiment_struct = extract_analyst_sentiment_struct(consensus)

    # current price אפשר למשוך מ-profile או מאנדפוינט אחר (quote).
    current_price = None
    if profile:
        current_price = profile.get("price") or profile.get("lastDiv")  # placeholder – תעדכן לפי הפרופיל האמיתי
    price_targets_struct = extract_price_targets_struct(price_target, current_price)

    today_str = date.today().isoformat()
    report_period_end = latest_block.get("period_end_date")

    payload = {
        "prompter_name": PROMPTER_NAME,
        "company": {
            "symbol": symbol,
            "company_name": profile.get("companyName") if profile else "",
            "sector": profile.get("sector") if profile else "",
            "industry": profile.get("industry") if profile else "",
            "country": profile.get("country") if profile else "",
            "market_cap": profile.get("mktCap") if profile else None,
        },
        "report_context": {
            "as_of_date": today_str,
            "latest_report_period_end": report_period_end,
            "previous_report_period_end": prev_block.get("period_end_date"),
            "fiscal_period_type": "quarter",
        },
        "latest_quarter": latest_block,
        "previous_quarter": prev_block,
        "derived_metrics": derived,
        "analyst_estimates": analyst_estimates_struct,
        "analyst_sentiment": analyst_sentiment_struct,
        "price_targets": price_targets_struct,
        "news": news,
    }

    meta = {
        "symbol": symbol,
        "as_of_date": today_str,
        "report_period_end_date": report_period_end,
        "sector": payload["company"]["sector"],
        "industry": payload["company"]["industry"],
    }

    return payload, meta


# ==========================
# Call GPT
# ==========================

def call_gpt_for_payload(payload: dict, system_prompt: str) -> dict | None:
    """
    שולח מערכת+JSON למודל ומחזיר את ה-JSON שהמודל מחזיר (אחרי json.loads).
    """
    try:
        response = client.chat.completions.create(
            model="gpt-5.1",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0.0,
        )
        content = response.choices[0].message.content.strip()
        # מצופה JSON בלבד לפי הפרומפטר
        return json.loads(content)
    except Exception as e:
        print(f"[GPT ERROR] {payload.get('company', {}).get('symbol')}: {e}")
        return None


# ==========================
# Save result in Supabase
# ==========================

def save_ai_score(meta: dict, ai_result: dict, input_payload: dict):
    """
    שומר רשומת ציון ב-fundamentals_ai_scores.
    meta: symbol, as_of_date, report_period_end_date, sector, industry
    ai_result: JSON שחזר מהמודל
    input_payload: מה שנשלח למודל (ל-debug)
    """
    row = {
        "symbol": meta["symbol"],
        "as_of_date": meta["as_of_date"],
        "report_period_end_date": meta["report_period_end_date"],
        "sector": meta.get("sector"),
        "industry": meta.get("industry"),
        "overall_score": ai_result.get("overall_score"),
        "growth_score": ai_result.get("growth_score"),
        "profitability_score": ai_result.get("profitability_score"),
        "risk_score": ai_result.get("risk_score"),
        "sentiment_score": ai_result.get("sentiment_score"),
        "summary": ai_result.get("summary"),
        "strengths": ai_result.get("strengths"),
        "weaknesses": ai_result.get("weaknesses"),
        "model_name": ai_result.get("model_name"),
        "run_id": None,
        "input_payload": input_payload,
        "output_payload": ai_result,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }

    supabase.table("fundamentals_ai_scores").upsert(row).execute()


# ==========================
# Main worker
# ==========================

def run_worker():
    system_prompt = load_system_prompt()
    symbols = get_symbols_from_earnings_calendar()
    print(f"Found {len(symbols)} symbols to process")

    for i, symbol in enumerate(symbols, start=1):
        print(f"[{i}/{len(symbols)}] Processing {symbol} ...")
        try:
            payload, meta = build_payload_for_symbol(symbol)
            if not payload:
                continue

            ai_result = call_gpt_for_payload(payload, system_prompt)
            if not ai_result:
                continue

            save_ai_score(meta, ai_result, payload)

            # הגנה בסיסית מפני rate-limit
            time.sleep(1.0)
        except Exception as e:
            print(f"[ERROR] Failed for {symbol}: {e}")
            # ממשיכים לסימבול הבא

    print("Done fundamentals AI worker.")


if __name__ == "__main__":
    run_worker()
