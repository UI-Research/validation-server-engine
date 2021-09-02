import base64
import boto3
import json
import numpy as np
import pandas as pd
import psycopg2
import re
import requests
import s3fs
import yaml

from botocore.exceptions import ClientError
from opendp.smartnoise.sql import PostgresReader, PrivateReader
from opendp.smartnoise.sql.privacy import Privacy
from opendp.smartnoise.metadata import CollectionMetadata

def get_secret(secret_name = "validation-server-backend"):
    """
    Retrieve database credentials from AWS Secrets Manager.
    """
    region_name = "us-east-1"

    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=region_name
    )

    # In this sample we only handle the specific exceptions for the 'GetSecretValue' API.
    # See https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
    # We rethrow the exception by default.

    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'DecryptionFailureException':
            # Secrets Manager can't decrypt the protected secret text using the provided KMS key.
            # Deal with the exception here, and/or rethrow at your discretion.
            raise e
        elif e.response['Error']['Code'] == 'InternalServiceErrorException':
            # An error occurred on the server side.
            # Deal with the exception here, and/or rethrow at your discretion.
            raise e
        elif e.response['Error']['Code'] == 'InvalidParameterException':
            # You provided an invalid value for a parameter.
            # Deal with the exception here, and/or rethrow at your discretion.
            raise e
        elif e.response['Error']['Code'] == 'InvalidRequestException':
            # You provided a parameter value that is not valid for the current state of the resource.
            # Deal with the exception here, and/or rethrow at your discretion.
            raise e
        elif e.response['Error']['Code'] == 'ResourceNotFoundException':
            # We can't find the resource that you asked for.
            # Deal with the exception here, and/or rethrow at your discretion.
            raise e
    else:
        # Decrypts secret using the associated KMS CMK.
        # Depending on whether the secret is a string or binary, one of these fields will be populated.
        if 'SecretString' in get_secret_value_response:
            secret = get_secret_value_response['SecretString']
        else:
            secret = base64.b64decode(get_secret_value_response['SecretBinary'])
        return json.loads(secret)

def load_metadata(metadata = "puf.json"):
    """
    Load base metadata file for SmartNoise reader.
    """
    client = boto3.client("s3")
    response = client.get_object(
        Bucket="ui-validation-server", 
        Key=f"data/{metadata}"
    )
    data = response["Body"].read()
    meta_dict = json.loads(data)
    meta = CollectionMetadata.from_dict(meta_dict)
    return meta

def get_reader(database, credentials):
    """
    Create a SmartNoise reader to the postgres database.
    """
    reader = PostgresReader(
        host = credentials["host"],
        database = database,
        user = credentials["username"],
        password = credentials["password"],
        port = credentials["port"]
    )

    return reader

def get_epsilon_per_column(reader, metadata, query, epsilon):
    """
    Calculate proper epsilon given query complexity.
    """
    private_reader = PrivateReader(reader, metadata, epsilon_per_column = 1.0)
    multiplier = len(private_reader.get_privacy_cost(query))
    epsilon_per_column = epsilon / multiplier

    return epsilon_per_column


def parse_payload(command_id, run_id, resarcher_id, epsilon, data, accuracy):
    """Parse query results to payload for POST request."""
    # hard-code quantiles
    quantiles = [0.10, 0.50, 0.90]
    
    # format accuracy and calculate quantiles
    accuracy = accuracy[0]
    accuracy = accuracy.dropna(axis=1, how = "all")
    accuracy = accuracy.dropna(axis=0, how = "any")
    accuracy = np.quantile(accuracy, quantiles)
    accuracy = pd.DataFrame(data = {"quantiles": quantiles, "accuracy": accuracy})
    accuracy = accuracy.to_json(orient = "records")

    # format results
    data = data.to_json(orient = "records")

    result = {
        "ok": True,
        "data": data
    }

    # generate api payload
    payload = {
        "command_id": command_id,
        "run_id": run_id,
        "researcher_id": resarcher_id,
        "privacy_budget_used": epsilon,
        "result": json.dumps(result),
        "accuracy": json.dumps(accuracy)
    }

    return payload

def get_table_name(query):
    """Parse the table name from a query."""
    table = re.search(r"puf\.puf_\S+", query).group(0)
    return table

def get_postgres_connector(credentials):
    """Connect to the postgres database"""
     # connect to database
    connection = psycopg2.connect(
    host = credentials["host"],
    dbname = "puf",
    user = credentials["username"],
    password = credentials["password"],
    port = credentials["port"]
    )
    return connection

def run_transformation_query(transformation_query, credentials):
    """Run a transformation query on the postgreSQL database."""
    # parse table name from statement
    table = get_table_name(transformation_query)

    # drop any old reference tables
    drop_statement = f"DROP TABLE IF EXISTS {table}"

    # connect to database
    connection = get_postgres_connector(credentials)

    with connection.cursor() as cursor:
        cursor.execute(drop_statement)
        cursor.execute(transformation_query)
    
    # close out connection
    connection.commit()
    connection.close()

def generate_transformation_metadata(analysis_query, credentials):
    """Get necessary metadata from a transformation for SmartNoise."""
    cardinality_cutoff = 100 # cutoff for "categorical" variable levels
    # parse table name from statement
    schema = get_table_name(analysis_query)
    table = schema.replace("puf.", "")
    
    # connect to database
    connection = get_postgres_connector(credentials)

    # get column names and types
    information_query = f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table}';"
    
    with connection.cursor() as cursor:
        cursor.execute(information_query)
        cols = cursor.fetchall()

    # start metadata
    puf = {}
    for col in cols:
        key = col[0]
        key_type = col[1]
        if key_type in ["smallint", "integer", "bigint"]:
            key_type = "int"
        elif key_type in ["decimal", "numeric", "real", "double precision"]:
            key_type = "float"
        elif key_type in ["boolean"]:
            key_type = "boolean"
        else:
            key = "string"
        puf[key] = {}
        puf[key]["type"] = key_type
    
    # get min/max/unique
    for key in puf.keys():
        metadata_query = f"""
        SELECT 
            min({key}) as min,
            max({key}) as max,
            count(distinct({key})) as ndistinct,
            count({key}) as count
        FROM puf.{table}
        """
        with connection.cursor() as cursor:
            cursor.execute(metadata_query)
            min, max, nunique, count = cursor.fetchall()[0]
            puf[key]["lower"] = min
            puf[key]["upper"] = max
            if nunique < cardinality_cutoff: 
                puf[key]["cardinality"] = nunique

    # catch recid
    puf["recid"] = {
        "type": "int",
        "private_id": True
    }

    # catch weird formatting
    puf["censor_dims"] = False
    puf["rows"] = count

    # generate full smartnoise metadata
    metadata = {
        "Database" : {
            "puf" : {
                f"{table}" : puf
            }
        }
    }

    return metadata

def run_analysis_query(reader, metadata, event):
    """
    Run an analysis query on the synthetic database table.
    """
    # hard coded (for now) parameters
    delta = 1/ (1000 * np.sqrt(1000))
    alphas = [0.05]

    # pull event parameters
    analysis_query = event["analysis_query"]
    command_id = event["command_id"]
    run_id = event["run_id"]
    researcher_id = event["researcher_id"]
    epsilon = float(event["epsilon"])

    # generate epsilon based on query complexity
    epsilon_per_column = get_epsilon_per_column(reader, metadata, analysis_query, epsilon)

    # set up SmartNoise
    privacy = Privacy(epsilon=epsilon_per_column, delta=delta, alphas=alphas)
    private_reader = PrivateReader(reader=reader, metadata=metadata, privacy=privacy)

    # run query and format results
    data, accuracy = private_reader.execute_with_accuracy_df(analysis_query)
    payload = parse_payload(command_id, run_id, researcher_id, epsilon, data, accuracy)

    return payload

def post_payload(event, payload, credentials, confidential_query):
    """Post results payload to API."""
    # set url
    url_stub = "https://validation-server-stg.urban.org/api/v1"
    
    if confidential_query:
        url = f"{url_stub}/confidential-data-result/"
    else:
        url = f"{url_stub}/synthetic-data-result/"

    # set header
    token = credentials["token"]
    headers = {"Authorization": f"Token {token}"}

    # post to api
    response = requests.post(url, headers=headers, data=payload)

    return response

def parse_error(event, e):
    """Generate payload when exception occurs."""
    # pull event parameters
    analysis_query = event["analysis_query"]
    command_id = event["command_id"]
    run_id = event["run_id"]
    researcher_id = event["researcher_id"]
    epsilon = float(event["epsilon"])

    result = {
        "ok": False,
        "error": str(e)
    }

    accuracy = {}

    # generate api payload
    payload = {
        "command_id": command_id,
        "run_id": run_id,
        "researcher_id": researcher_id,
        "privacy_budget_used": epsilon,
        "result": json.dumps(result),
        "accuracy": json.dumps(accuracy)
    }

    return payload

def handler(event, context):
    """Main lambda function."""
    print(event)
    try:
        # setup
        metadata = load_metadata()
        credentials = get_secret(secret_name = "validation-server-backend")
        reader = get_reader("puf", credentials)
        confidential_query = event["confidential_query"]
        transformation_query = event["transformation_query"]
        debug = event["debug"]
        # run transformation query
        if transformation_query is not None and not confidential_query:
            run_transformation_query(transformation_query, credentials)
        # regenerate metadata if necessary
        if transformation_query is not None:
            metadata = generate_transformation_metadata(transformation_query, credentials)
        # run analysis query
        payload = run_analysis_query(reader, metadata, event)
    except Exception as e:
        payload = parse_error(event, e)
    finally:
        # post to api
        if not debug:
            response = post_payload(event, payload, credentials, confidential_query)
            print(response.status_code)
            print(response.reason)
    print(payload)