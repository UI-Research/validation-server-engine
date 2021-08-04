# validation-server-backend

### deploy

Run `sam package` locally as a workaround since CodeBuild fails for no clear reason.

```bash
ACCOUNT_ID=$(aws sts get-caller-identity | jq ".Account" | sed 's/\"//g')
S3_BUCKET=aws-codestar-us-east-1-672001523455-validation-serv-pipe
sam build
sam package --s3-bucket $S3_BUCKET --output-template-file template-export.yml --image-repository ${ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/validation-server-engine
```

Then commit the new template-export up to GitHub

### invoke

```python
import boto3
import json

client = boto3.client("lambda")

payload = {
    "command_id": 2,
    "run_id": 1,
    "confidential_query": False,
    "epsilon": 1.00,
    "transformation_query": None,
    "analysis_query": "SELECT MARS, COUNT(E00200) as n FROM puf.puf GROUP BY MARS"
}

payload = json.dumps(payload).encode()

response = client.invoke(
    FunctionName="validation-server-engine",
    InvocationType="Event"
)
```

```bash
docker exec -it /bin/bash
mysql --user=[username] --password
mysql
```

```sql
USE mysql_data;
DELETE FROM v1_syntheticdataresult WHERE run_id=1;
```



