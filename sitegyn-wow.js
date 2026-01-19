(function () {
  if (!window.__SITEGYN__ || !window.__SITEGYN__.showWow) return;

  setTimeout(() => {
    const box = document.createElement("div");
    box.style.cssText = `
      position:fixed;
      bottom:24px;
      right:24px;
      background:#0f172a;
      color:white;
      padding:18px 22px;
      border-radius:16px;
      box-shadow:0 20px 40px rgba(0,0,0,.35);
      z-index:9999;
      display:flex;
      gap:12px;
      align-items:center;
      animation:fadeIn .6s ease;
    `;

    box.innerHTML = `
      <strong>🎉 Your site is live!</strong>
      <button id="editSiteBtn"
        style="margin-left:12px;
               background:#2dd4d8;
               border:none;
               padding:8px 14px;
               border-radius:999px;
               cursor:pointer;
               font-weight:700;">
        Edit Site
      </button>
    `;

    document.body.appendChild(box);

    document.getElementById("editSiteBtn").onclick = () => {
      window.location.href = "/editor?project=" + window.__SITEGYN__.projectId;
    };

    setTimeout(() => box.remove(), 12000);
  }, 1800);
})();
