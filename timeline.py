import json
import codecs
import logging
import datetime
import iso8601
import tzlocal
import boto3
from collections import defaultdict
from boto3.dynamodb.conditions import Key
from botocore.errorfactory import ClientError

logging.basicConfig(level=logging.WARN)
logger = logging.getLogger()

local_tz = tzlocal.get_localzone()


def create_incident_table(db):
    try:
        incidents = db.create_table(
            TableName='Incidents',
            KeySchema=[
                {
                    'AttributeName': 'incident_key',
                    'KeyType': 'HASH'
                }
            ],
            AttributeDefinitions=[
                {
                    'AttributeName': 'incident_key',
                    'AttributeType': 'S'
                },
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 10,
                'WriteCapacityUnits': 10
            }
            )
    except ClientError as ex:
        if ex.response['Error']['Code'] == 'ResourceInUseException':
            incidents = db.Table('Incidents')
        else:
            raise

    return incidents


def create_timeline_table(db):
    try:
        timelines = db.create_table(
            TableName='Timelines',
            KeySchema=[
                {
                    'AttributeName': 'incident_key',
                    'KeyType': 'HASH'
                },
                {
                    'AttributeName': 'timestamp',
                    'KeyType': 'RANGE'
                }
            ],
            AttributeDefinitions=[
                {
                    'AttributeName': 'incident_key',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'timestamp',
                    'AttributeType': 'S'
                }
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 10,
                'WriteCapacityUnits': 10
            }
        )
    except ClientError as ex:
        if ex.response['Error']['Code'] == 'ResourceInUseException':
            timelines = db.Table('Timelines')
        else:
            raise

    return timelines


def load_event(filename):
    with codecs.open(filename, 'r', 'utf-8') as f:
        event = json.load(f)
        event['updated'] = datetime.datetime.now().isoformat()
    return event


def is_existing_incident(incident_table, e):
    try:
        r = incident_table.get_item(Key={'incident_key': e['incident_key']})
        return 'Item' in r
    except ClientError:
        return False


def get_incident(incident_table, e):
    r = incident_table.get_item(Key={'incident_key': e['incident_key']})
    return r['Item']


def create_incident(incident_table, e):
    e['created'] = e['updated']
    r = incident_table.put_item(Item=e)
    return r


def delete_incident(incident_table, e):
    r = incident_table.delete_item(Key={'incident_key': e['incident_key']})
    return r


def create_timeline(timeline_table, u):
    r = timeline_table.put_item(Item=u)
    return r


def delete_timeline(timeline_table, u):
    r = timeline_table.delete_item(Key={'timestamp': u['timestamp'],
                                        'incident_key': u['incident_key']})
    return r


def update_incident(incident_table, e):
    r = incident_table.update_item(
        Key={'incident_key': e['incident_key']},
        ExpressionAttributeNames={
            '#u': 'updated'
        },
        UpdateExpression='SET #u = :u',
        ExpressionAttributeValues={
            ':u': e['updated']
        },
        ReturnValues='ALL_NEW'
    )
    if 'triggers' in e:
        r = incident_table.update_item(
            Key={'incident_key': e['incident_key']},
            ExpressionAttributeNames={
                '#t': 'triggers',
            },
            UpdateExpression='SET #t = :t',
            ExpressionAttributeValues={
                ':t': e['triggers']
            },
            ReturnValues='ALL_NEW'
        )

    return r


def compare_events(e1, e2):
    changes = defaultdict(dict)

    if 'triggers' not in e1:
        e1['triggers'] = []
    if 'triggers' not in e2:
        e2['triggers'] = []

    for trigger1 in e1['triggers']:
        trigger2 = [x for x in e2['triggers'] if x['criteria'] == trigger1['criteria']]
        if trigger2:
            trigger2 = trigger2[0]
            for source1 in trigger1['sources']:
                source2 = [x for x in trigger2['sources'] if x['name'] == source1['name']]
                if not source2:
                    changes[trigger1['criteria']][source1['name']] = 'resolved'
        else:
            for source1 in trigger1['sources']:
                changes[trigger1['criteria']][source1['name']] = 'resolved'

    for trigger2 in e2['triggers']:
        trigger1 = [x for x in e1['triggers'] if x['criteria'] == trigger2['criteria']]
        if trigger1:
            trigger1 = trigger1[0]
            for source2 in trigger2['sources']:
                source1 = [x for x in trigger1['sources'] if x['name'] == source2['name']]
                if not source1:
                    changes[trigger2['criteria']][source2['name']] = 'triggered'
        else:
            for source2 in trigger2['sources']:
                changes[trigger2['criteria']][source2['name']] = 'triggered'

    updates = {'timestamp': e2['updated'],
               'incident_key': e2['incident_key'],
               'changes': []}
    for criteria_name in changes.keys():
        update = defaultdict(list)
        update['criteria'] = criteria_name
        for source_name, status in changes[criteria_name].items():
            update[status].append(source_name)
        updates['changes'].append(dict(update))

    return updates


def first_update(e):
    updates = {'timestamp': e['updated'],
               'incident_key': e['incident_key'],
               'changes': []}

    for trigger in e['triggers']:
        update = {'criteria': trigger['criteria'],
                  'triggered': [x['name'] for x in trigger['sources']]}
        updates['changes'].append(update)

    return updates


def compute_update(incident_table, e):
    if is_existing_incident(incident_table, e):
        last_event = get_incident(incident_table, e)
        return compare_events(last_event, e)
    else:
        return first_update(e)


def is_resolved(e):
    return e['event_type'] == 'resolve'


def process_event(incident_table, timeline_table, e):

    if 'updated' not in e:
        e['updated'] = datetime.datetime.now().isoformat()

    if is_resolved(e):
        last_event = get_incident(incident_table, e)
        update_incident(incident_table, e)
        create_timeline(timeline_table, compare_events(last_event, e))
        timeline = get_timeline(timeline_table, e)
        clear_timeline(timeline_table, e)
        delete_incident(incident_table, e)
        return timeline
    elif is_existing_incident(incident_table, e):
        last_event = get_incident(incident_table, e)
        update_incident(incident_table, e)
        create_timeline(timeline_table, compare_events(last_event, e))
        return get_timeline(timeline_table, e)
    else:
        create_incident(incident_table, e)
        create_timeline(timeline_table, first_update(e))
        return get_timeline(timeline_table, e)


def get_timeline(timeline_table, e):
    r = timeline_table.scan(
        FilterExpression=Key('incident_key').eq(e['incident_key'])
    )
    return r['Items']


def clear_timeline(timeline_table, e):
    for item in get_timeline(timeline_table, e):
        delete_timeline(timeline_table, item)
