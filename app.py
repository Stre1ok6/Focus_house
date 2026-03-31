from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for

from focus_engine import DEFAULT_CONFIG, FocusAnalyzer, StreamingSessionManager
from focus_engine.goal_profiles import DEFAULT_GOAL_TEXT, GOAL_EXAMPLES, GOAL_PLACEHOLDER, normalize_goal_input


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
app.secret_key = os.environ.get("FOCUS_APP_SECRET", "focus-app-dev-secret")

analyzer = FocusAnalyzer(DEFAULT_CONFIG)
session_manager = StreamingSessionManager(analyzer, DEFAULT_CONFIG)


def runtime_snapshot() -> dict:
    ready, engine = analyzer.configure_tesseract()
    scoring = analyzer.scoring_status()
    return {
        "ocr_ready": ready,
        "ocr_engine": engine,
        "llm_ready": bool(scoring["configured"]),
        "llm_provider": str(scoring["provider"]),
        "llm_model": str(scoring["model"]),
        "workers": DEFAULT_CONFIG.max_workers,
        "max_uploads": DEFAULT_CONFIG.max_uploads,
        "session_window_size": DEFAULT_CONFIG.session_window_size,
        "session_capture_mode": "continuous",
        "session_default_duration_minutes": DEFAULT_CONFIG.session_default_duration_minutes,
        "session_min_duration_minutes": DEFAULT_CONFIG.session_min_duration_minutes,
        "session_max_duration_minutes": DEFAULT_CONFIG.session_max_duration_minutes,
        "supported_extensions": ", ".join(DEFAULT_CONFIG.supported_extensions),
        "goal_placeholder": GOAL_PLACEHOLDER,
        "goal_examples": GOAL_EXAMPLES,
        "default_goal": DEFAULT_GOAL_TEXT,
    }


def collect_uploads(files) -> list[tuple[str, bytes]]:
    uploads: list[tuple[str, bytes]] = []
    for file in files[: DEFAULT_CONFIG.max_uploads]:
        if not file or not file.filename:
            continue
        if not analyzer.allowed_file(file.filename):
            continue
        uploads.append((file.filename, file.read()))
    return uploads


def extract_goal_from_request() -> str:
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        return normalize_goal_input(payload.get("goal", ""))
    return normalize_goal_input(request.form.get("goal", ""))


def extract_int_from_request(field: str, default: int | None = None) -> int | None:
    raw_value = None
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        raw_value = payload.get(field)
    else:
        raw_value = request.form.get(field)

    if raw_value in (None, ""):
        return default

    try:
        return int(raw_value)
    except (TypeError, ValueError):
        raise ValueError(field) from None


def normalize_selected_goal(goal: str | None) -> str:
    return normalize_goal_input(goal) or DEFAULT_GOAL_TEXT


def error_redirect(message: str, selected_goal: str | None = None):
    return redirect(url_for("home", error=message, selected_goal=normalize_selected_goal(selected_goal)))


@app.get("/")
def home():
    selected_goal = normalize_selected_goal(request.args.get("selected_goal"))
    return render_template(
        "index.html",
        report=None,
        runtime=runtime_snapshot(),
        generated_at=datetime.now(),
        error=request.args.get("error"),
        selected_goal=selected_goal,
    )


@app.post("/analyze")
def analyze():
    goal = normalize_goal_input(request.form.get("goal", ""))
    files = request.files.getlist("screenshots")

    if not goal:
        return error_redirect("请填写本次专注目标。")
    if not files:
        return error_redirect("请至少上传一张截图。", goal)

    uploads = collect_uploads(files)
    if not uploads:
        return error_redirect("上传的文件格式不支持，或未读取到有效截图。", goal)

    report = analyzer.analyze_uploads(goal, uploads)
    return render_template(
        "index.html",
        report=report,
        runtime=runtime_snapshot(),
        generated_at=datetime.now(),
        error=None,
        selected_goal=goal,
    )


@app.post("/api/analyze")
def analyze_api():
    goal = extract_goal_from_request()
    if not goal:
        return jsonify({"error": "missing_goal"}), 400

    uploads: list[tuple[str, bytes]] = []
    if request.files:
        uploads = collect_uploads(request.files.getlist("screenshots"))
    elif request.is_json:
        uploads = []

    if not uploads:
        return jsonify({"error": "missing_screenshots"}), 400

    return jsonify(analyzer.analyze_uploads(goal, uploads))


@app.post("/api/session/start")
def start_session():
    goal = extract_goal_from_request()
    if not goal:
        return jsonify({"error": "missing_goal"}), 400
    payload_data = request.get_json(silent=True) or {}
    monitoring_mode = payload_data.get("monitoring_mode", "medium")
    try:
        duration_minutes = extract_int_from_request("duration_minutes")
    except ValueError as exc:
        return jsonify({"error": f"invalid_{exc.args[0]}"}), 400

    try:
        # 【修改调用方式】传入 monitoring_mode
        payload = session_manager.start(
            goal, 
            duration_minutes=duration_minutes, 
            monitoring_mode=monitoring_mode
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(payload)


@app.post("/api/session/<session_id>/frame")
def push_frame(session_id: str):
    file = request.files.get("screenshot")
    if not file or not file.filename:
        return jsonify({"error": "missing_screenshot"}), 400
    if not analyzer.allowed_file(file.filename):
        return jsonify({"error": "unsupported_file_type"}), 400
    try:
        payload = session_manager.add_frame(session_id, file.filename, file.read())
    except KeyError:
        return jsonify({"error": "session_not_found"}), 404
    except RuntimeError:
        return jsonify({"error": "session_not_running"}), 409
    return jsonify(payload)


@app.get("/api/session/<session_id>")
def session_snapshot(session_id: str):
    try:
        payload = session_manager.snapshot(session_id)
    except KeyError:
        return jsonify({"error": "session_not_found"}), 404
    return jsonify(payload)


@app.post("/api/session/<session_id>/complete")
def complete_session(session_id: str):
    try:
        payload = session_manager.complete(session_id)
    except KeyError:
        return jsonify({"error": "session_not_found"}), 404
    return jsonify(payload)


@app.get("/api/analyze-demo")
def analyze_demo():
    sample_path = Path("测试.png")
    if not sample_path.exists():
        return jsonify({"error": "demo_sample_missing"}), 404
    return jsonify(analyzer.analyze_uploads(DEFAULT_GOAL_TEXT, [(sample_path.name, sample_path.read_bytes())]))


@app.get("/health")
def health():
    return jsonify({"status": "ok", "runtime": runtime_snapshot()})


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)


