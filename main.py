"""识语绘声 Web 服务的本地启动入口。"""

import uvicorn


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=9000, reload=True)
