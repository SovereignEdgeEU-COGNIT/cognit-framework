import ast
from typing import Any
import pyone
from fastapi import HTTPException, status
from haversine import haversine, Unit

ONE_XMLRPC = None  # Set when importing module
DOCUMENT_TYPES = {
    'APP_REQUIREMENT': 1338,
    'FUNCTION': 1339
}

def _parse_geolocation(geo_str: str) -> tuple[float, float]:
    lat_str, lon_str = geo_str.split(",")
    return float(lat_str.strip()), float(lon_str.strip())

def create_client(user: str, password: str) -> pyone.OneServer:
    return pyone.OneServer(ONE_XMLRPC, session=f"{user}:{password}")


def authenticate(user: str, password: str) -> bool:
    one = create_client(user, password)

    try:
        one.userpool.info()
    except pyone.OneAuthenticationException as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=e)


def app_requirement_create(one: pyone.OneServer, app_requirement: dict) -> int:
    document_id = validate_call(lambda: one.document.allocate(
        app_requirement, DOCUMENT_TYPES['APP_REQUIREMENT']))

    return document_id


def app_requirement_get(one: pyone.OneServer, document_id: int) -> dict:
    document = document_get(
        one, document_id, 'APP_REQUIREMENT')
    return dict(document.TEMPLATE)


def app_requirement_update(one: pyone.OneServer, document_id: int, app_requirement: dict):
    app_requirement_get(one, document_id)

    validate_call(lambda: one.document.update(document_id, app_requirement, 0))


def app_requirement_delete(one: pyone.OneServer, document_id: int):
    app_requirement_get(one, document_id)

    validate_call(lambda: one.document.delete(document_id))


def function_create(one: pyone.OneServer,  function: dict) -> int:
    # get list of function documents for this user
    # https://docs.opennebula.io/6.8/integration_and_development/system_interfaces/api.html#one-documentpool-info
    documents = validate_call(lambda:
                              one.documentpool.info(-3, -1, -1, DOCUMENT_TYPES['FUNCTION']))

    for document in documents.DOCUMENT:
        document_body = dict(document.TEMPLATE)

        # do not re-upload already existing functions
        if document_body['FC_HASH'] == function['FC_HASH']:
            return document.ID

    document_id = validate_call(lambda: one.document.allocate(
        function, DOCUMENT_TYPES['FUNCTION']))
    return document_id


def function_get(one: pyone.OneServer, document_id: int) -> dict:
    document = document_get(one, document_id, 'FUNCTION')
    return dict(document.TEMPLATE)

def clusters_ids_get(
    one: pyone.OneServer,
    geolocation: str,
    flavour: str,
    is_confidential: str | None = None,
    providers: str | list[str] | None = None,
    target_cardinality: str | None = None,
) -> list[int]:
    """Return cluster IDs sorted by distance and filtered by optional capabilities.

    Parameters:
        one: Authenticated OpenNebula client.
        geolocation: Device coordinates formatted as "lat,lon".
        flavour: Requested runtime flavour.
        is_confidential: String "true"/"false" indicating if only confidential clusters should be considered.
        providers: List of acceptable provider identifiers, or string representation of list.
        target_cardinality: String representation of target cardinality of the cluster.

    Returns:
        List of cluster IDs ordered by distance from the device location.
    """
    clusters = one.clusterpool.info()
    if isinstance(geolocation, str):
        device_geolocation = _parse_geolocation(geolocation)
    else:
        device_geolocation = geolocation

    # Convert string parameters to appropriate types
    requested_is_confidential = None
    if is_confidential is not None:
        requested_is_confidential = is_confidential.lower() == "true"

    requested_providers = set()
    if providers:
        # Handle both list objects and string representations
        if isinstance(providers, list):
            requested_providers = set(providers)
        elif isinstance(providers, str):
            try:
                # Parse string representation of list like "['provider_1']"
                parsed_providers = ast.literal_eval(providers)
                if isinstance(parsed_providers, list):
                    requested_providers = set(parsed_providers)
            except (ValueError, SyntaxError):
                # If parsing fails, treat as comma-separated string
                requested_providers = set(p.strip() for p in providers.split(",") if p.strip())

    requested_target_cardinality = None
    if target_cardinality:
        try:
            requested_target_cardinality = int(target_cardinality)
        except ValueError:
            requested_target_cardinality = None

    cluster_distances = []

    for cluster in clusters.CLUSTER:
        template = cluster.TEMPLATE
        supported_flavours = [item.strip() for item in template.get("FLAVOURS", "").split(",") if item.strip()]

        if flavour not in supported_flavours:
            continue

        if requested_is_confidential is not None:
            cluster_is_confidential_str = template.get("IS_CONFIDENTIAL", "")
            if cluster_is_confidential_str:
                cluster_confidential = cluster_is_confidential_str.lower() == "true"
                if requested_is_confidential != cluster_confidential:
                    continue

        if requested_providers:
            cluster_provider = template.get("PROVIDER", "")
            if cluster_provider not in requested_providers:
                continue

        if requested_target_cardinality is not None:
            cluster_max_capacity_str = template.get("MAX_CAPACITY", "")
            if cluster_max_capacity_str:
                try:
                    cluster_max_capacity = int(cluster_max_capacity_str)
                    if requested_target_cardinality > cluster_max_capacity:
                        continue
                except ValueError:
                    # Skip cluster if MAX_CAPACITY is not a valid integer
                    continue

        cluster_geolocation = template.get("GEOLOCATION", "")
        if cluster_geolocation:
            try:
                cluster_coords = _parse_geolocation(cluster_geolocation)
                distance = haversine(device_geolocation, cluster_coords, unit=Unit.KILOMETERS)
                cluster_distances.append((cluster.ID, distance))
            except Exception as e:
                continue

    sorted_clusters = sorted(cluster_distances, key=lambda x: x[1])
    return [cid for cid, _ in sorted_clusters]


def cluster_get(one: pyone.OneServer, cluster_id: int, flavour: str) -> dict:
    cluster = validate_call(lambda: one.cluster.info(cluster_id))

    # Add the information about the flavour in the cluster endpoint
    edge_cluster_frontend_endpoint = cluster.TEMPLATE.get('EDGE_CLUSTER_FRONTEND') + '/' + flavour
    cluster.TEMPLATE['EDGE_CLUSTER_FRONTEND'] = edge_cluster_frontend_endpoint
    
    return {
        'ID': cluster_id,
        'NAME': cluster.NAME,
        'HOSTS': cluster.HOSTS.ID,
        'DATASTORES': cluster.DATASTORES.ID,
        'VNETS': cluster.VNETS.ID,
        'TEMPLATE': dict(cluster.TEMPLATE)
    }

# Helpers


def document_get(one: pyone.OneServer, document_id: int, type_str: str):
    document = validate_call(lambda: one.document.info(document_id))

    type = DOCUMENT_TYPES[type_str]

    if int(document.TYPE) != type:
        e = f"Resource {document_id} is not of type {type_str}"
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED, detail=e)

    return document


def validate_call(xmlrpc_call):
    try:
        return xmlrpc_call()
    except pyone.OneAuthenticationException as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except pyone.OneAuthorizationException as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except pyone.OneNoExistsException as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))