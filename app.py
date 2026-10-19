import json
from pathlib import Path

import numpy as np
import requests
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

BASE = Path(__file__).parent
app = Flask(__name__, static_folder="static", static_url_path="")

OLLAMA = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
CHAT_MODEL = "llama3.2:3b"
CONTEXT_MIN = 0.50   # a topic must score at least this to be used as context

topics = json.loads((BASE / "data" / "topics.json").read_text(encoding="utf-8"))
contacts = json.loads((BASE / "data" / "contacts.json").read_text(encoding="utf-8"))
states = json.loads((BASE / "data" / "states.json").read_text(encoding="utf-8"))

SYSTEM_PROMPT = """You are a legal-information assistant for ordinary people in India.
Explain rights and next steps in simple, short, kind language (easy to read, no legal jargon).
Reply in the same language the user wrote in.

Rules:
1. Use ONLY facts found in CONTEXT. Verified guides are reliable. Web snippets may be outdated, so prefer verified guides.
2. If CONTEXT does not cover the question, say you are not fully sure and tell the person to call the free NALSA legal aid helpline 15100.
3. NEVER invent law section numbers, phone numbers, fees, deadlines or website links.
4. If the person may be in danger, the first line must tell them to call 112.
5. You are not a lawyer. Do not promise outcomes.
6. Be neutral. Never blame any gender, religion, caste or group.
7. If the user's state is given, add one line saying rules and offices can differ by state and they should check with their local legal services authority.

Format:
- One short summary sentence.
- "What you can do:" followed by at most 5 numbered steps.
- "Keep ready:" followed by a short list of documents or proof, only if relevant.
Keep the whole answer under 200 words."""


def embed(texts):
    res = requests.post(f"{OLLAMA}/api/embed", json={"model": EMBED_MODEL, "input": texts}, timeout=60)
    res.raise_for_status()
    vecs = np.array(res.json()["embeddings"], dtype=np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def topic_text(t):
    return f"search_document: {t['title']}. {t['title']}. {' '.join(t['tags'])}. {t['right']}"


print("Embedding topics with Ollama...")
try:
    topic_vectors = embed([topic_text(t) for t in topics])
except requests.exceptions.RequestException as e:
    raise SystemExit(f"Could not reach Ollama. Is it running? Did you run 'ollama pull {EMBED_MODEL}'?\n{e}")


def web_search(q):
    try:
        from ddgs import DDGS
        rows = DDGS().text(f"{q} India law rights", region="in-en", max_results=4)
        return [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")} for r in rows]
    except Exception as e:
        print("web search failed:", e)
        return []


def build_context(matched, web):
    parts = []
    for i, t in enumerate(matched, 1):
        steps = "\n".join(f"- {s}" for s in t["steps"])
        parts.append(f"[Verified guide {i}] {t['title']}\nRight: {t['right']}\nSteps:\n{steps}")
    for i, w in enumerate(web, 1):
        parts.append(f"[Web snippet {i}] {w['title']}: {w['snippet']}")
    return "\n\n".join(parts) if parts else "(No verified guide matched this question.)"


def line(obj):
    return json.dumps(obj, ensure_ascii=False) + "\n"


@app.get("/")
def home():
    return send_from_directory("static", "index.html")


@app.get("/api/ask")
def ask():
    q = request.args.get("q", "").strip()
    state = request.args.get("state", "").strip()
    use_web = request.args.get("web", "0") == "1"
    if len(q) < 2:
        return jsonify({"error": "empty question"}), 400

    def generate():
        # 1. Retrieve verified topics
        matched = []
        try:
            qv = embed([f"search_query: {q}"])[0]
            scores = topic_vectors @ qv
            order = np.argsort(-scores)[:2]
            print(f"q={q!r} top={[(topics[i]['id'], round(float(scores[i]), 2)) for i in order]}")
            matched = [topics[i] for i in order if scores[i] >= CONTEXT_MIN and scores[i] >= scores[order[0]] - 0.04]
        except requests.exceptions.RequestException:
            pass

        # 2. Optional live web snippets
        web = web_search(q) if use_web else []

        # 3. Verified helplines and state help (never from the LLM)
        nums = []
        for t in matched:
            for n in t["helplines"]:
                if n not in nums:
                    nums.append(n)
        if not nums:
            nums = ["15100", "112"]
        help_objs = [c for n in nums for c in contacts if c["number"] == n]
        state_obj = next((s for s in states if s["state"] == state), None)

        yield line({
            "type": "meta",
            "helplines": help_objs,
            "state": state_obj,
            "sources": [{"title": w["title"], "url": w["url"]} for w in web],
            "matched": [t["title"] for t in matched],
        })

        # 4. Stream the LLM answer
        user_msg = (
            f"User's state: {state or 'not given'}\n\n"
            f"CONTEXT:\n{build_context(matched, web)}\n\n"
            f"QUESTION: {q}"
        )
        payload = {
            "model": CHAT_MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_msg}],
            "stream": True,
            "keep_alive": "30m",
            "options": {"temperature": 0.2, "num_ctx": 2048, "num_predict": 320},
        }
        try:
            with requests.post(f"{OLLAMA}/api/chat", json=payload, stream=True, timeout=300) as r:
                if r.status_code == 404:
                    yield line({"type": "error", "text": f"Model not found. Run: ollama pull {CHAT_MODEL}"})
                    return
                r.raise_for_status()
                for raw in r.iter_lines():
                    if not raw:
                        continue
                    chunk = json.loads(raw)
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        yield line({"type": "token", "text": piece})
                    if chunk.get("done"):
                        break
        except requests.exceptions.RequestException as e:
            yield line({"type": "error", "text": f"Could not reach Ollama: {e}"})

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")


@app.get("/api/contacts")
def get_contacts():
    return jsonify(contacts)


@app.get("/api/states")
def get_states():
    return jsonify(states)

import threading


def warm_up():
    """Load the chat model into memory at startup so the first user is not slow."""
    try:
        requests.post(f"{OLLAMA}/api/chat", json={
            "model": CHAT_MODEL,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False, "keep_alive": "30m",
            "options": {"num_predict": 1, "num_ctx": 2048},
        }, timeout=300)
        print("Chat model warmed up.")
    except Exception as e:
        print("warm-up skipped:", e)


threading.Thread(target=warm_up, daemon=True).start()

if __name__ == "__main__":
    app.run(debug=False, port=5000)
