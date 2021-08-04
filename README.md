# validation-server-backend

### deploy

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
    FunctionName="awscodestar-validation-serv-lambda-QueryFunction",
    InvocationType="Event",

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



