
# Override root route to always return something
from fastapi.responses import HTMLResponse

@app.get("/", response_class=HTMLResponse)
async def root():
    from pathlib import Path
    for name in ["index.html", "dashboard-v3.html", "dashboard-v2.html"]:
        if Path(name).exists():
            return HTMLResponse(Path(name).read_text())
    return HTMLResponse("""<!DOCTYPE html>
<html><head><title>Pollen</title></head>
<body style="font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;background:#FBFBFA">
<div style="text-align:center">
<h1>🌸 Pollen</h1>
<p>API is running. <a href="/docs">API Docs</a> | <a href="/onboard">Onboard</a></p>
<p style="margin-top:16px;color:#999;font-size:13px">Try: <code>/api/jobs?limit=10</code></p>
</div></body></html>""")
