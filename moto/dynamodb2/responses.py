from __future__ import unicode_literals
import json
import six

from moto.core.responses import BaseResponse
from moto.core.utils import camelcase_to_underscores
from .models import dynamodb_backend2, dynamo_json_dump


GET_SESSION_TOKEN_RESULT = """
<GetSessionTokenResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">
 <GetSessionTokenResult>
 <Credentials>
 <SessionToken>
 AQoEXAMPLEH4aoAH0gNCAPyJxz4BlCFFxWNE1OPTgk5TthT+FvwqnKwRcOIfrRh3c/L
 To6UDdyJwOOvEVPvLXCrrrUtdnniCEXAMPLE/IvU1dYUg2RVAJBanLiHb4IgRmpRV3z
 rkuWJOgQs8IZZaIv2BXIa2R4OlgkBN9bkUDNCJiBeb/AXlzBBko7b15fjrBs2+cTQtp
 Z3CYWFXG8C5zqx37wnOE49mRl/+OtkIKGO7fAE
 </SessionToken>
 <SecretAccessKey>
 wJalrXUtnFEMI/K7MDENG/bPxRfiCYzEXAMPLEKEY
 </SecretAccessKey>
 <Expiration>2011-07-11T19:55:29.611Z</Expiration>
 <AccessKeyId>AKIAIOSFODNN7EXAMPLE</AccessKeyId>
 </Credentials>
 </GetSessionTokenResult>
 <ResponseMetadata>
 <RequestId>58c5dbae-abef-11e0-8cfe-09039844ac7d</RequestId>
 </ResponseMetadata>
</GetSessionTokenResponse>"""


def sts_handler():
    return GET_SESSION_TOKEN_RESULT


class DynamoHandler(BaseResponse):

    def get_endpoint_name(self, headers):
        """Parses request headers and extracts part od the X-Amz-Target
        that corresponds to a method of DynamoHandler

        ie: X-Amz-Target: DynamoDB_20111205.ListTables -> ListTables
        """
        # Headers are case-insensitive. Probably a better way to do this.
        match = headers.get('x-amz-target') or headers.get('X-Amz-Target')
        if match:
            return match.split(".")[1]

    def error(self, type_, status=400):
        return status, self.response_headers, dynamo_json_dump({'__type': type_})

    def call_action(self):
        body = self.body.decode('utf-8')
        if 'GetSessionToken' in body:
            return 200, self.response_headers, sts_handler()

        self.body = json.loads(body or '{}')
        endpoint = self.get_endpoint_name(self.headers)
        if endpoint:
            endpoint = camelcase_to_underscores(endpoint)
            response = getattr(self, endpoint)()
            if isinstance(response, six.string_types):
                return 200, self.response_headers, response

            else:
                status_code, new_headers, response_content = response
                self.response_headers.update(new_headers)
                return status_code, self.response_headers, response_content
        else:
            return 404, self.response_headers, ""

    def list_tables(self):
        body = self.body
        limit = body.get('Limit')
        if body.get("ExclusiveStartTableName"):
            last = body.get("ExclusiveStartTableName")
            start = list(dynamodb_backend2.tables.keys()).index(last) + 1
        else:
            start = 0
        all_tables = list(dynamodb_backend2.tables.keys())
        if limit:
            tables = all_tables[start:start + limit]
        else:
            tables = all_tables[start:]
        response = {"TableNames": tables}
        if limit and len(all_tables) > start + limit:
            response["LastEvaluatedTableName"] = tables[-1]
        return dynamo_json_dump(response)

    def create_table(self):
        body = self.body
        # get the table name
        table_name = body['TableName']
        # get the throughput
        throughput = body["ProvisionedThroughput"]
        # getting the schema
        key_schema = body['KeySchema']
        # getting attribute definition
        attr = body["AttributeDefinitions"]
        # getting the indexes
        global_indexes = body.get("GlobalSecondaryIndexes", [])

        table = dynamodb_backend2.create_table(table_name,
                   schema=key_schema,
                   throughput=throughput,
                   attr=attr,
                   global_indexes=global_indexes)
        if table is not None:
            return dynamo_json_dump(table.describe)
        else:
            er = 'com.amazonaws.dynamodb.v20111205#ResourceInUseException'
            return self.error(er)

    def delete_table(self):
        name = self.body['TableName']
        table = dynamodb_backend2.delete_table(name)
        if table is not None:
            return dynamo_json_dump(table.describe)
        else:
            er = 'com.amazonaws.dynamodb.v20111205#ResourceNotFoundException'
            return self.error(er)

    def update_table(self):
        name = self.body['TableName']
        if 'GlobalSecondaryIndexUpdates' in self.body:
            table = dynamodb_backend2.update_table_global_indexes(name, self.body['GlobalSecondaryIndexUpdates'])
        if 'ProvisionedThroughput' in self.body:
            throughput = self.body["ProvisionedThroughput"]
            table = dynamodb_backend2.update_table_throughput(name, throughput)
        return dynamo_json_dump(table.describe)

    def describe_table(self):
        name = self.body['TableName']
        try:
            table = dynamodb_backend2.tables[name]
        except KeyError:
            er = 'com.amazonaws.dynamodb.v20111205#ResourceNotFoundException'
            return self.error(er)
        return dynamo_json_dump(table.describe)

    def put_item(self):
        name = self.body['TableName']
        item = self.body['Item']
        overwrite = 'Expected' not in self.body
        if not overwrite:
            expected = self.body['Expected']
        else:
            expected = None

        try:
            result = dynamodb_backend2.put_item(name, item, expected, overwrite)
        except Exception:
            er = 'com.amazonaws.dynamodb.v20111205#ConditionalCheckFailedException'
            return self.error(er)

        if result:
            item_dict = result.to_json()
            item_dict['ConsumedCapacityUnits'] = 1
            return dynamo_json_dump(item_dict)
        else:
            er = 'com.amazonaws.dynamodb.v20111205#ResourceNotFoundException'
            return self.error(er)

    def batch_write_item(self):
        table_batches = self.body['RequestItems']

        for table_name, table_requests in table_batches.items():
            for table_request in table_requests:
                request_type = list(table_request.keys())[0]
                request = list(table_request.values())[0]
                if request_type == 'PutRequest':
                    item = request['Item']
                    dynamodb_backend2.put_item(table_name, item)
                elif request_type == 'DeleteRequest':
                    keys = request['Key']
                    item = dynamodb_backend2.delete_item(table_name, keys)

        response = {
            "Responses": {
                "Thread": {
                    "ConsumedCapacityUnits": 1.0
                },
                "Reply": {
                    "ConsumedCapacityUnits": 1.0
                }
            },
            "UnprocessedItems": {}
        }

        return dynamo_json_dump(response)

    def get_item(self):
        name = self.body['TableName']
        key = self.body['Key']
        try:
            item = dynamodb_backend2.get_item(name, key)
        except ValueError:
            er = 'com.amazon.coral.validate#ValidationException'
            return self.error(er, status=400)
        if item:
            item_dict = item.describe_attrs(attributes=None)
            item_dict['ConsumedCapacityUnits'] = 0.5
            return dynamo_json_dump(item_dict)
        else:
            # Item not found
            er = '{}'
            return self.error(er, status=200)

    def batch_get_item(self):
        table_batches = self.body['RequestItems']

        results = {
            "ConsumedCapacity": [],
            "Responses": {
            },
            "UnprocessedKeys": {
            }
        }

        for table_name, table_request in table_batches.items():
            keys = table_request['Keys']
            attributes_to_get = table_request.get('AttributesToGet')
            results["Responses"][table_name] = []
            for key in keys:
                item = dynamodb_backend2.get_item(table_name, key)
                if item:
                    item_describe = item.describe_attrs(attributes_to_get)
                    results["Responses"][table_name].append(item_describe["Item"])

            results["ConsumedCapacity"].append({
                "CapacityUnits": len(keys),
                "TableName": table_name
            })
        return dynamo_json_dump(results)

    def query(self):
        name = self.body['TableName']

        # {u'KeyConditionExpression': u'#n0 = :v0', u'ExpressionAttributeValues': {u':v0': {u'S': u'johndoe'}}, u'ExpressionAttributeNames': {u'#n0': u'username'}}
        key_condition_expression = self.body.get('KeyConditionExpression')
        if key_condition_expression:
            value_alias_map = self.body['ExpressionAttributeValues']

            table = dynamodb_backend2.get_table(name)
            index_name = self.body.get('IndexName')
            if index_name:
                all_indexes = (table.global_indexes or []) + (table.indexes or [])
                indexes_by_name = dict((i['IndexName'], i) for i in all_indexes)
                if index_name not in indexes_by_name:
                    raise ValueError('Invalid index: %s for table: %s. Available indexes are: %s' % (
                        index_name, name, ', '.join(indexes_by_name.keys())
                    ))

                index = indexes_by_name[index_name]['KeySchema']
            else:
                index = table.schema

            key_map = [column for _, column in sorted((k, v) for k, v in self.body['ExpressionAttributeNames'].items())]

            if " AND " in key_condition_expression:
                expressions = key_condition_expression.split(" AND ", 1)

                index_hash_key = [key for key in index if key['KeyType'] == 'HASH'][0]
                hash_key_index_in_key_map = key_map.index(index_hash_key['AttributeName'])

                hash_key_expression = expressions.pop(hash_key_index_in_key_map).strip('()')
                # TODO implement more than one range expression and OR operators
                range_key_expression = expressions[0].strip('()')
                range_key_expression_components = range_key_expression.split()
                range_comparison = range_key_expression_components[1]
                if 'AND' in range_key_expression:
                    range_comparison = 'BETWEEN'
                    range_values = [
                        value_alias_map[range_key_expression_components[2]],
                        value_alias_map[range_key_expression_components[4]],
                    ]
                elif 'begins_with' in range_key_expression:
                    range_comparison = 'BEGINS_WITH'
                    range_values = [
                        value_alias_map[range_key_expression_components[1]],
                    ]
                else:
                    range_values = [value_alias_map[range_key_expression_components[2]]]
            else:
                hash_key_expression = key_condition_expression
                range_comparison = None
                range_values = []

            hash_key_value_alias = hash_key_expression.split("=")[1].strip()
            hash_key = value_alias_map[hash_key_value_alias]
        else:
            # 'KeyConditions': {u'forum_name': {u'ComparisonOperator': u'EQ', u'AttributeValueList': [{u'S': u'the-key'}]}}
            key_conditions = self.body.get('KeyConditions')
            if key_conditions:
                hash_key_name, range_key_name = dynamodb_backend2.get_table_keys_name(name, key_conditions.keys())
                if hash_key_name is None:
                    er = "'com.amazonaws.dynamodb.v20120810#ResourceNotFoundException"
                    return self.error(er)
                hash_key = key_conditions[hash_key_name]['AttributeValueList'][0]
                if len(key_conditions) == 1:
                    range_comparison = None
                    range_values = []
                else:
                    if range_key_name is None:
                        er = "com.amazon.coral.validate#ValidationException"
                        return self.error(er)
                    else:
                        range_condition = key_conditions[range_key_name]
                        if range_condition:
                            range_comparison = range_condition['ComparisonOperator']
                            range_values = range_condition['AttributeValueList']
                        else:
                            range_comparison = None
                            range_values = []

        index_name = self.body.get('IndexName')
        items, last_page = dynamodb_backend2.query(name, hash_key, range_comparison, range_values, index_name=index_name)
        if items is None:
            er = 'com.amazonaws.dynamodb.v20111205#ResourceNotFoundException'
            return self.error(er)

        reversed = self.body.get("ScanIndexForward")
        if reversed is False:
            items.reverse()

        limit = self.body.get("Limit")
        if limit:
            items = items[:limit]

        result = {
            "Count": len(items),
            "Items": [item.attrs for item in items],
            "ConsumedCapacityUnits": 1,
        }

        # Implement this when we do pagination
        # if not last_page:
        #     result["LastEvaluatedKey"] = {
        #         "HashKeyElement": items[-1].hash_key,
        #         "RangeKeyElement": items[-1].range_key,
        #     }
        return dynamo_json_dump(result)

    def scan(self):
        name = self.body['TableName']

        filters = {}
        scan_filters = self.body.get('ScanFilter', {})
        for attribute_name, scan_filter in scan_filters.items():
            # Keys are attribute names. Values are tuples of (comparison, comparison_value)
            comparison_operator = scan_filter["ComparisonOperator"]
            comparison_values = scan_filter.get("AttributeValueList", [])
            filters[attribute_name] = (comparison_operator, comparison_values)

        items, scanned_count, last_page = dynamodb_backend2.scan(name, filters)

        if items is None:
            er = 'com.amazonaws.dynamodb.v20111205#ResourceNotFoundException'
            return self.error(er)

        limit = self.body.get("Limit")
        if limit:
            items = items[:limit]

        result = {
            "Count": len(items),
            "Items": [item.attrs for item in items],
            "ConsumedCapacityUnits": 1,
            "ScannedCount": scanned_count
        }

        # Implement this when we do pagination
        # if not last_page:
        #     result["LastEvaluatedKey"] = {
        #         "HashKeyElement": items[-1].hash_key,
        #         "RangeKeyElement": items[-1].range_key,
        #     }
        return dynamo_json_dump(result)

    def delete_item(self):
        name = self.body['TableName']
        keys = self.body['Key']
        return_values = self.body.get('ReturnValues', '')
        item = dynamodb_backend2.delete_item(name, keys)
        if item:
            if return_values == 'ALL_OLD':
                item_dict = item.to_json()
            else:
                item_dict = {'Attributes': {}}
            item_dict['ConsumedCapacityUnits'] = 0.5
            return dynamo_json_dump(item_dict)
        else:
            er = 'com.amazonaws.dynamodb.v20120810#ConditionalCheckFailedException'
            return self.error(er)

    def update_item(self):
        name = self.body['TableName']
        key = self.body['Key']
        update_expression = self.body.get('UpdateExpression')
        attribute_updates = self.body.get('AttributeUpdates')
        item = dynamodb_backend2.update_item(name, key, update_expression, attribute_updates)

        item_dict = item.to_json()
        item_dict['ConsumedCapacityUnits'] = 0.5
        return dynamo_json_dump(item_dict)
