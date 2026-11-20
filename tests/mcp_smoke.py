"""End-to-end check of the MCP server as a real client sees it, over stdio and Streamable HTTP.

  python tests/mcp_smoke.py /path/to/short_clip.mov /tmp/nookcut-smoke
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

SERVER = os.path.join(os.path.dirname(sys.executable), "nookcut-mcp")


def text(result):
    return "".join(getattr(c, "text", "") for c in result.content)


async def exercise(session: ClientSession, clip: str | None, project: str | None):
    await session.initialize()
    tools = [t.name for t in (await session.list_tools()).tools]
    prompts = [p.name for p in (await session.list_prompts()).prompts]
    resources = [str(r.uri) for r in (await session.list_resources()).resources]
    print(f"  {len(tools)} tools, prompts={prompts}, resources={resources}")
    skill = await session.read_resource("nookcut://skill")
    assert "NookCut editor" in skill.contents[0].text
    assert {"auto_edit", "suggest_copy", "get_transcript", "get_frames", "verify_video", "list_broll"} <= set(tools), tools
    assert not [t for t in tools if t.startswith("_")], tools
    prompt = await session.get_prompt("nookcut-editor", {"source": "x.mov", "title": "Test"})
    assert "Make a short from this brief" in prompt.messages[0].content.text
    env = json.loads(text(await session.call_tool("check_environment", {})))
    print(f"  environment ok={env['ok']}, speech-to-text ready={env['speech_to_text']['ready']}")
    when = json.loads(text(await session.call_tool("best_posting_times", {"region": "India"})))
    print(f"  posting window: {when['windows'][0]['audience_time']}")
    if not clip:
        return
    r = await session.call_tool("create_project", {"project_dir": project, "source": clip,
                                                   "title": "Smoke test", "max_duration": 20})
    assert not getattr(r, "is_error", getattr(r, "isError", False)), text(r)
    job = json.loads(text(await session.call_tool("analyze_video", {"project_dir": project, "wait": False})))
    seen = set()
    while True:
        st = json.loads(text(await session.call_tool("job_status", {"job_id": job["job_id"]})))
        if st["status"] != "running":
            break
        if "progress" in st:
            seen.add(st['progress'].get('stage', 'starting'))
        await asyncio.sleep(0.5)
    print(f"  background analyze: {st['status']} in {st['elapsed_s']}s, stages seen: {sorted(seen)}")
    assert st["status"] == "done", st
    speech = st["result"]["speech"]
    print(f"  speech: {speech.get('words', 0)} words ({speech.get('note') or speech.get('model')})")
    if speech.get("words"):
        tr = json.loads(text(await session.call_tool("get_transcript", {"project_dir": project})))
        print(f"  get_transcript: {len(tr['sentences'])} sentences, first {tr['sentences'][0]['text']!r}")
    plan = json.loads(text(await session.call_tool("plan_edit", {"project_dir": project})))
    print(f"  plan: {plan['template']} {plan['length']}s, {len(plan['segments'])} segments")
    copy = json.loads(text(await session.call_tool("suggest_copy", {"project_dir": project})))
    assert copy["source"].endswith("(offline)") and copy["titles"] and copy["hashtags"], copy
    print(f"  suggest_copy: {copy['titles'][0]!r}, file {copy['file_name']}")
    prev = await session.call_tool("preview_plan", {"project_dir": project})
    kinds = [c.type for c in prev.content]
    print(f"  preview content: {kinds}")
    assert "image" in kinds

    # readable errors
    bad = await session.call_tool("render_video", {"project_dir": project + "-missing"})
    assert is_error(bad) and "[input]" in text(bad), text(bad)
    print(f"  error text: {text(bad).splitlines()[0]!r}")

    # progress + busy + cancel on a real render
    job = json.loads(text(await session.call_tool("render_video", {"project_dir": project, "wait": False})))
    while True:
        st = json.loads(text(await session.call_tool("job_status", {"job_id": job["job_id"]})))
        if st["status"] != "running" or st.get("progress", {}).get("stage") == "rendering segments":
            break
        await asyncio.sleep(0.2)
    busy = await session.call_tool("render_video", {"project_dir": project})
    assert is_error(busy) and "[busy]" in text(busy), text(busy)
    print(f"  second render while busy: {text(busy).splitlines()[0]!r}")
    print(f"  render progress: {st.get('progress')}")
    c = json.loads(text(await session.call_tool("cancel_job", {"job_id": job["job_id"]})))
    await asyncio.sleep(1)
    st = json.loads(text(await session.call_tool("job_status", {"job_id": job["job_id"]})))
    print(f"  cancel_job -> {c['cancelled']}, status {st['status']}, ffmpeg left: {ffmpeg_count()}")
    assert st["status"] == "cancelled" and ffmpeg_count() == 0, st

    # one-shot offline path for small/local models, with loose inputs
    auto = await session.call_tool("auto_edit", {"source": clip, "title": "Smoke auto", "project_dir": project + "-auto",
                                                 "max_duration": "20s", "orientation": "9:16", "platforms": "shorts, tiktok",
                                                 "region": "India"})
    assert not is_error(auto), text(auto)
    auto = json.loads(text(auto))
    assert os.path.getsize(auto["video"]) > 0 and os.path.exists(auto["publish_kit"]), auto
    print(f"  auto_edit: {auto['summary']} -> {os.path.basename(auto['video'])}")
    assert "checks" in auto and os.path.exists(auto["checks"]["frames"]), auto
    print(f"  checks: ok={auto['checks']['ok']} {auto['checks']['facts']} {auto['checks']['issues']}")
    seen = await session.call_tool("get_frames", {"project_dir": project + "-auto", "times": ["0:01", 3], "count": 2})
    assert not is_error(seen) and "image" in [c.type for c in seen.content], text(seen)
    src = await session.call_tool("get_frames", {"project_dir": project + "-auto", "source": True, "count": 4})
    assert not is_error(src), text(src)
    ver = await session.call_tool("verify_video", {"project_dir": project + "-auto"})
    assert not is_error(ver) and "image" in [c.type for c in ver.content], text(ver)
    print("  get_frames (render + source) and verify_video: images returned")
    nob = await session.call_tool("list_broll", {"project_dir": project + "-auto"})
    assert is_error(nob) and "B-roll folder" in text(nob), text(nob)

    # leave a render running and disconnect: the server must stop it
    await session.call_tool("render_video", {"project_dir": project, "wait": False})
    await asyncio.sleep(2)
    print(f"  render running before disconnect, ffmpeg: {ffmpeg_count()}")


def is_error(r):
    return getattr(r, "is_error", getattr(r, "isError", False))


def ffmpeg_count():
    return len(subprocess.run(["pgrep", "-f", "ffmpeg.*nookcut_"], capture_output=True, text=True).stdout.split())


async def run_stdio(clip, project):
    print("stdio:")
    async with stdio_client(StdioServerParameters(command=SERVER)) as (r, w):
        async with ClientSession(r, w) as s:
            await exercise(s, clip, project)
    if clip:
        time.sleep(4)
        print(f"  after disconnect, ffmpeg left: {ffmpeg_count()}")
        assert ffmpeg_count() == 0


async def run_http(port: int, token: str):
    print("http:")
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=b"{}", method="POST"))
    except urllib.error.HTTPError as e:
        print(f"  no token -> {e.code}")
        assert e.code == 401
    client = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with streamable_http_client(url, http_client=client) as streams:
        r, w = streams[0], streams[1]
        async with ClientSession(r, w) as s:
            await exercise(s, None, None)


def main():
    clip = sys.argv[1] if len(sys.argv) > 1 else None
    project = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(run_stdio(clip, project))
    port, token = 8799, "smoke-token"
    proc = subprocess.Popen([SERVER, "--transport", "http", "--port", str(port)],
                            env={**os.environ, "NOOKCUT_TOKEN": token})
    try:
        time.sleep(2.5)
        asyncio.run(run_http(port, token))
    finally:
        proc.terminate()
    print("OK")


if __name__ == "__main__":
    main()
