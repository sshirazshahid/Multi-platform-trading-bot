def load_knowledge():
    p = Path("data/knowledge_model.json")
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
