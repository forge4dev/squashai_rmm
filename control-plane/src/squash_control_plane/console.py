from importlib.resources import files

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(include_in_schema=False)


def _asset(name: str) -> FileResponse:
    path = files("squash_control_plane").joinpath("console_assets", name)
    return FileResponse(path)


@router.get("/console")
def console_index() -> FileResponse:
    return _asset("index.html")


@router.get("/console/app.js")
def console_javascript() -> FileResponse:
    return _asset("app.js")


@router.get("/console/styles.css")
def console_stylesheet() -> FileResponse:
    return _asset("styles.css")
