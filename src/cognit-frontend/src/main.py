#!/usr/bin/env python

from fastapi.responses import RedirectResponse
from fastapi import FastAPI, status, HTTPException, Header, Path, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from typing import Annotated, Any, List
import uvicorn
import re
import requests
import json
import logging

import cognit_conf as conf
import biscuit_token as auth
import opennebula as one
from cognit_models import AppRequirements, EdgeClusterFrontend, ExecSyncParams

one.ONE_XMLRPC = conf.ONE_XMLRPC

logger = logging.getLogger("uvicorn")
if conf.LOG_LEVEL == 'debug':  # uvicorn run log parameter is ignored
    logger.setLevel(logging.DEBUG)

# TODO: Update design doc

app = FastAPI(title='Cognit Frontend', version='0.1.0')


@app.get("/")
async def root():
    return RedirectResponse(url="/docs")


@app.post("/v1/authenticate", status_code=status.HTTP_201_CREATED)
async def authenticate(credentials: Annotated[HTTPBasicCredentials, Depends(HTTPBasic())]) -> str:
    one.authenticate(credentials.username, credentials.password)

    token = auth.generate_token(credentials.username, credentials.password)
    return token


@app.get("/v1/public_key", status_code=status.HTTP_200_OK)
async def get_public_key() -> str:

    return auth.PUBLIC_KEY


@app.post("/v1/app_requirements", status_code=status.HTTP_200_OK)
async def upload_application_requirements(
    requirements: AppRequirements,
    token: Annotated[str | None, Header()] = None
) -> int:

    client = authorize(token)

    return one.app_requirement_create(client, requirements.model_dump())


@app.put("/v1/app_requirements/{id}", status_code=status.HTTP_200_OK)
async def update_application_requirements(
    id: Annotated[int, Path(title="Document ID of the App Requirement")],
    requirements: AppRequirements,
    token: Annotated[str | None, Header()] = None
):

    client = authorize(token)

    one.app_requirement_update(client, id, requirements.model_dump())


@app.get("/v1/app_requirements/{id}", status_code=status.HTTP_200_OK, response_model=AppRequirements)
async def get_application_requirements(
    id: Annotated[int, Path(title="Document ID of the App Requirement")],
    token: Annotated[str | None, Header()] = None
) -> Any:

    client = authorize(token)

    return one.app_requirement_get(client, id)


@app.delete("/v1/app_requirements/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_application_requirements(
    id: Annotated[int, Path(title="Document ID of the App Requirement")],
    token: Annotated[str | None, Header()] = None
):

    client = authorize(token)

    return one.app_requirement_delete(client, id)


@app.get("/v1/app_requirements/{id}/ec_fe", status_code=status.HTTP_200_OK)
async def get_edge_cluster_frontends(
    id: Annotated[int, Path(title="Document ID of the App Requirement")],
    token: Annotated[str | None, Header()] = None
) -> List[EdgeClusterFrontend]:

    client = authorize(token)
    app_reqs = one.app_requirement_get(client, id)

    # Flavour from the device runtime
    flavour = app_reqs['FLAVOUR']

    # Get cluster IDs filtered by flavour support and sorted by distance from device
    cluster_ids = one.clusters_ids_get(client, app_reqs['GEOLOCATION'], flavour)

    # Get cluster information with flavour-specific endpoint and supported flavours
    clusters = []
    for cluster_id in cluster_ids:
        clusters.append(one.cluster_get(client, cluster_id, flavour))
    
    return clusters


@app.post("/v1/daas/upload", status_code=status.HTTP_200_OK)
async def upload_function(
    function: ExecSyncParams,
    token: Annotated[str | None, Header()] = None
) -> int:

    client = authorize(token)

    return one.function_create(client, function.model_dump())

if __name__ == "__main__":
    uvicorn.run("main:app", host=conf.HOST, port=conf.PORT,
                reload=False, log_level=conf.LOG_LEVEL)


def authorize(token) -> list:
    if token is None:
        message = 'Missing token in header'
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    try:
        facts = auth.authorize_token(token)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    credentials = []
    matchers = [r'user\("([^"]*)"\)', r'password\("([^"]*)"\)']

    for regexp in matchers:
        match = re.search(regexp, facts)
        value = match.group(1) if match else None

        credentials.append(value)

    return one.create_client(credentials[0], credentials[1])
