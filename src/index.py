import base64
import boto3
import json
import numpy as np
import pandas as pd
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

def load_metadata(metadata = "puf_subset.yaml"):
    """
    Load base metadata file for SmartNoise reader.
    """
    client = boto3.client("s3")
    response = client.get_object(
        Bucket="ui-validation-server", 
        Key=f"data/{metadata}"
    )
    data = response["Body"].read()
    meta_dict = yaml.safe_load(data)
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


def parse_payload(command_id, run_id, epsilon, data, accuracy):

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
        "privacy_budget_used": epsilon,
        "result": json.dumps(result),
        "accuracy": json.dumps(accuracy)
    }

    return payload

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
    epsilon = float(event["epsilon"])

    # generate epsilon based on query complexity
    epsilon_per_column = get_epsilon_per_column(reader, metadata, analysis_query, epsilon)

    # set up SmartNoise
    privacy = Privacy(epsilon=epsilon_per_column, delta=delta, alphas=alphas)
    private_reader = PrivateReader(reader=reader, metadata=metadata, privacy=privacy)

    # run query and format results
    data, accuracy = private_reader.execute_with_accuracy_df(analysis_query)
    payload = parse_payload(command_id, run_id, epsilon, data, accuracy)

    return payload

def post_payload(event, payload, credentials, confidential_query):
    
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

def handler(event, context):
    
    # setup
    metadata = load_metadata()
    credentials = get_secret(secret_name = "validation-server-backend")
    reader = get_reader("puf", credentials)
    confidential_query = event["confidential_query"]
    
    # run analysis query
    payload = run_analysis_query(reader, metadata, event)

    # post to api
    response = post_payload(event, payload, credentials, confidential_query)
    print(response.ok)
    print(response.status_code)