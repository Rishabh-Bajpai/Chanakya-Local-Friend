from chatflash import create_app

app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("run_webapp:app", host="127.0.0.1", port=18550, reload=False)
