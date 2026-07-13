from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "services.qmd_history_gateway.app:app",
        host=os.environ.get("QMD_HISTORY_HOST", "127.0.0.1"),
        port=int(os.environ.get("QMD_HISTORY_PORT", "8112")),
        reload=False,
    )
