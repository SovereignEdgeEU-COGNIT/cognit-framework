#!/usr/bin/env python

from fastapi.responses import RedirectResponse
from fastapi import FastAPI, status, HTTPException, Header, Path, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from typing import Annotated, Any, List, Optional
import uvicorn
import re

import cognit_conf as conf
import biscuit_token as auth
import opennebula as one
import db_manager
from cognit_models import AppRequirements, EdgeClusterFrontend, ExecSyncParams
from cognit_logger import setup_logging, get_logger

one.ONE_XMLRPC = conf.ONE_XMLRPC

# Setup centralized logging
setup_logging(conf.LOG_LEVEL)
logger = get_logger(__name__)

# TODO: Update design doc

# Initialize database
db = db_manager.DBManager(conf.DB_PATH, conf.DB_CLEANUP_DAYS)


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
    device_id: Optional[str] = app_reqs.get("ID")

    # Backward compatibility with the older device-runtime: fallback to cluster selection if ID is not in the app requirements
    if not device_id or device_id == 'None':
        logger.info("No device ID found in the app requirements")
        flavour = app_reqs['FLAVOUR']
        cluster_ids = one.clusters_ids_get(
            client,
            app_reqs['GEOLOCATION'],
            flavour,
            app_reqs.get('IS_CONFIDENTIAL'),
            app_reqs.get('PROVIDERS'),
            app_reqs.get('MAX_CAPACITY'),
        )
        clusters = []
        for cluster_id in cluster_ids:
            clusters.append(one.cluster_get(client, cluster_id, flavour))
        return clusters

    flavour = app_reqs['FLAVOUR']
    cached_device_assignment = db.get_device_assignment(device_id, flavour)
    if cached_device_assignment and cached_device_assignment['app_req_json'] == app_reqs:
        logger.info("App requirements are the same as the cached ones")
        db.update_last_seen(device_id, flavour)
        cluster = one.cluster_get(client, int(cached_device_assignment['cluster_id']), cached_device_assignment['flavour'])
        return [cluster]
    elif not cached_device_assignment:
        logger.info("No cached device assignment found")
        # Select the best cluster for this device based on requirements
        cluster_ids = one.clusters_ids_get(
            client,
            app_reqs['GEOLOCATION'],
            flavour,
            app_reqs.get('IS_CONFIDENTIAL'),
            app_reqs.get('PROVIDERS')
        )

        if not cluster_ids:
            # No clusters match the requirements
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No clusters available matching the requirements"
            )

        # Use the best (closest) cluster
        selected_cluster_id = cluster_ids[0]
        db.insert_device_assignment(device_id, selected_cluster_id, flavour, id, app_reqs)
        cluster = one.cluster_get(client, selected_cluster_id, flavour)
        return [cluster]
    else:
        # App requirements changed, need to find a new cluster
        logger.info("App requirements changed, selecting new cluster")
        cluster_ids = one.clusters_ids_get(
            client,
            app_reqs['GEOLOCATION'],
            flavour,
            app_reqs.get('IS_CONFIDENTIAL'),
            app_reqs.get('PROVIDERS')
        )

        if not cluster_ids:
            # No clusters match the new requirements
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No clusters available matching the new requirements"
            )

        # Use the best (closest) cluster
        selected_cluster_id = cluster_ids[0]
        db.update_device_assignment(device_id, selected_cluster_id, flavour, id, app_reqs)
        cluster = one.cluster_get(client, selected_cluster_id, flavour)
        return [cluster]


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
