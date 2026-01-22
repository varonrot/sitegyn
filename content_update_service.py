# ==========================================
# Content Update Service
# Endpoint: POST /api/content/update
# ==========================================

@app.route("/api/content/update", methods=["POST"])
def update_content():
    try:
        data = request.get_json(force=True)

        project_id = data.get("project_id")
        user_message = (data.get("message") or "").strip()

        if not project_id or not user_message:
            return jsonify({"error": "missing_project_id_or_message"}), 400

        # ==========================
        # Load project
        # ==========================
        project = (
            supabase.table("projects")
            .select("id, content_json, selected_template_id")
            .eq("id", project_id)
            .single()
            .execute()
            .data
        )

        if not project:
            return jsonify({"error": "project_not_found"}), 404

        current_content = project.get("content_json")
        template_id = project.get("selected_template_id")

        if not current_content or not template_id:
            return jsonify({"error": "content_or_template_missing"}), 400

        template_conf = TEMPLATES.get(template_id)
        if not template_conf:
            return jsonify({"error": "template_not_found"}), 400

        # ==========================
        # Load schema
        # ==========================
        base_dir = Path(__file__).resolve().parent
        schema_path = base_dir / template_conf["schema"]
        schema_str = schema_path.read_text(encoding="utf-8")

        # ==========================
        # Generate PATCH
        # ==========================
        patch_prompt = (
            Path(__file__).parent / "content_improve_prompt.txt"
        ).read_text(encoding="utf-8")

        final_prompt = (
            patch_prompt
            .replace("CURRENT_CONTENT_JSON", json.dumps(current_content, ensure_ascii=False))
            .replace("SCHEMA_JSON", schema_str)
            .replace("USER_MESSAGE", user_message)
            .replace("CHAT_HISTORY", "[]")
        )

        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": final_prompt}],
            temperature=0.0,
        )

        patch_obj = json.loads(completion.choices[0].message.content.strip())

        if patch_obj.get("mode") != "patch":
            return jsonify({"error": "invalid_patch"}), 400

        # ==========================
        # Apply patch
        # ==========================
        updated_content = apply_patch_to_content(
            current_content=current_content,
            patch_obj=patch_obj
        )

        # ==========================
        # Save to Supabase
        # ==========================
        supabase.table("projects") \
            .update({"content_json": updated_content}) \
            .eq("id", project_id) \
            .execute()

        return jsonify({
            "reply": "✨ Done! I’ve updated the page based on your input.",
            "patched": True
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

 
