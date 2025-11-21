# config/templates_config.py

TEMPLATES = {
    # =====================
    # PIZZA
    # =====================
    "template_pizza_01": {
        "html": "sitegyn/templates/template_pizza_01/template_pizza_01.html",
        "mapping": "sitegyn/templates/template_pizza_01/template_pizza_01_mapping.json",
        # נשתמש בפרומפט הכללי שנמצא ב-root: content_fill_prompt.txt
        # ולכן לא צריך content_prompt מותאם אישית כרגע
        # "content_prompt": "content_fill_prompt.txt",
        "schema": "sitegyn/templates/template_pizza_01/template_pizza_01_schema.json",
    },
}
