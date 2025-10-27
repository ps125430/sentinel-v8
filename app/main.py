from fastapi import FastAPI
app = FastAPI(title="auto-deploy-ultra")
@app.get("/", include_in_schema=False)
def root():
    return {"status": "ok", "message": "AutoDeploy: Render pulls from GitHub on every push."}