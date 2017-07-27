import os
import json
import codecs
import logging
import jinja2
import requests
import sendgrid
import iso8601
import datetime
import tzlocal
import boto3
from timeline import process_event, create_incident_table, create_timeline_table
from dotmap import DotMap
from sendgrid.helpers.mail import Email, Mail, Content, Personalization

OUTLYER_URL = 'https://api.outlyer.com/v1'

# logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()
logger.setLevel(logging.INFO)

local_tz = tzlocal.get_localzone()

if os.getenv('DYNAMODB_LOCAL'):
    dynamodb = boto3.resource('dynamodb', endpoint_url='http://localhost:8000')
else:
    dynamodb = boto3.resource('dynamodb')

incident_table = create_incident_table(dynamodb)
timeline_table = create_timeline_table(dynamodb)

operator_map = {
    "eq": "equal to",
    "ne": "not equal to",
    "gt": "greater than",
    "ge": "greater than or equal to",
    "lt": "less than",
    "le": "less than or equal to"
}


class Event:
    def __init__(self, event_json):

        self._json = event_json

        self.event = DotMap(self._json)
        self.api_key = self.event.api_key
        self.rule_id = self.event.entity_id
        self.org_id = self.event.org
        self.account_id = self.event.account

        if not self.api_key:
            raise ValueError("API key not found in event payload")
        if not self.org_id:
            raise ValueError("Org ID not found in event payload")
        if not self.account_id:
            raise ValueError("Account ID not found in event payload")
        if not self.rule_id:
            raise ValueError("Rule ID not found in event payload")

        self.color = self._get_color()
        self.criteria = self._get_criteria()
        self.agents = self._get_agents()

        self._replace_sources()

        self.timeline = process_event(incident_table, timeline_table, self._json)
        self.event.updated = self._json['updated']

        for t in self.timeline:
            for change in t['changes']:
                for status in ('triggered', 'resolved'):
                    if status in change:
                        change[status] = [[x.id for x in self.agents.values() if x.name == name][0]
                                          for name in change[status]]
                pass

        logger.debug('Full event object available to templates:\n' + json_dump(self.__dict__))

    def _get_criteria(self):
        url = '{0}/orgs/{1}/accounts/{2}/rules/{3}/criteria'.format(OUTLYER_URL, self.org_id, self.account_id,
                                                                    self.rule_id)
        headers = {'Authorization': 'Bearer ' + self.api_key, 'Accept': 'application/json'}
        r = requests.get(url, headers=headers)
        r.raise_for_status()

        criteria = [DotMap(x) for x in r.json()]
        for crit in criteria:
            if crit.data_type == 'number':
                crit.description = "Metric '{}' has been {} threshold {} for {} seconds".format(
                    crit.metric, operator_map[crit.condition.operator], crit.condition.threshold, crit.condition.timeout
                )
            else:
                crit.description = "{} has been down for {} seconds".format(
                    crit.metric, crit.condition.timeout
                )

        return criteria

    def _get_agents(self):
        url = '{0}/orgs/{1}/accounts/{2}/agents'.format(OUTLYER_URL, self.org_id, self.account_id)
        headers = {'Authorization': 'Bearer ' + self.api_key, 'Accept': 'application/json'}
        r = requests.get(url, headers=headers)
        r.raise_for_status()
        agents = {x['id']: DotMap(x) for x in r.json()}
        return agents

    def _get_color(self):
        if self.event.message_type == 'CRITICAL':
            return 'red'
        elif self.event.message_type == 'RECOVERY':
            return 'green'
        else:
            return '#ccc'

    # noinspection PyUnresolvedReferences
    def _replace_sources(self):
        for crit in self.criteria:
            sources = []
            for source_id, state in crit.sources.iteritems():
                sources.append(DotMap({
                    "id": source_id,
                    "state": state,
                    "agent": self.agents[source_id]
                }))
            crit.sources = sources

    @property
    def mime_type(self):
        return self.event.format or 'text'

    @property
    def template_ext(self):
        return self.mime_type.replace('text', 'txt')

    def to_dict(self):
        return {
            'event': self.event.toDict(),
            'agents': self.agents,
            'criteria': [x.toDict() for x in self.criteria]
        }


# noinspection PyUnusedLocal
def compose_email(event_obj, context, subject, plain_text, html_text):
    message = Mail()
    message.from_email = Email("alerts@outlyer.com", "Outlyer Alerts")
    message.subject = subject
    message.add_content(Content("text/plain", plain_text))
    message.add_content(Content("text/html", html_text))

    p = Personalization()
    for recip in event_obj.event.to:
        p.add_to(Email(recip.email, recip.name))
    for recip in event_obj.event.cc:
        p.add_cc(Email(recip.email, recip.name))
    for recip in event_obj.event.bcc:
        p.add_bcc(Email(recip.email, recip.name))
    p.add_bcc(Email("alerts+cc@outlyer.com"))
    message.add_personalization(p)

    return message.get()


def send_email(event_obj, context, subject, plain_text, html_text):
    message = compose_email(event_obj, context, subject, plain_text, html_text)
    logger.debug('Sending message:\n' + json_dump(message))

    client = sendgrid.SendGridAPIClient(apikey=os.getenv('SENDGRID_API_KEY'))
    response = client.client.mail.send.post(request_body=message)

    result = {
        "status_code": response.status_code,
        "headers": {k: response.headers[k] for k in response.headers.keys()},
        "body": response.body.decode('utf-8')
    }
    logger.info('Final result:\n' + json_dump(result))
    return result


class DateTimeEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, datetime.datetime):
            return o.isoformat()

        return json.JSONEncoder.default(self, o)


def json_dump(obj):
    return json.dumps(obj, cls=DateTimeEncoder, sort_keys=True,
                      indent=4, separators=(',', ': '))


def datetimeformat(value, format_str='%H:%M / %d-%m-%Y'):
    if isinstance(value, str):
        value = iso8601.parse_date(value, local_tz)
    return value.strftime(format_str)


# noinspection PyUnusedLocal
def render_subject(event_obj, context):
    template = jinja2.Template(event_obj.event.subject)
    return template.render(event_obj.__dict__)


# noinspection PyUnusedLocal
def render_html(event_obj, context):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader('.'))
    env.filters['datetimeformat'] = datetimeformat

    template = env.get_template("email.html")
    return template.render(event_obj.__dict__)


# noinspection PyUnusedLocal
def render_plain(event_obj, context):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader('.'))
    env.filters['datetimeformat'] = datetimeformat

    template = env.get_template("email.txt")
    return template.render(event_obj.__dict__)


def lambda_handler(event, context):
    logger.debug("Payload received:\n" + json_dump(event))

    e = Event(event)
    html = render_html(e, context)
    text = render_plain(e, context)
    subject = render_subject(e, context)
    return send_email(e, context, subject, text, html)


def local_handler(event, context):
    logger.debug("Payload received:\n" + json_dump(event))

    e = Event(event)
    with open(context['basename'] + '_out.json', 'w') as f:
        f.write(json_dump(e.__dict__))

    html = render_html(e, context)
    return html


def local_dump(basename):
    with codecs.open(basename + ".json", "r", "utf-8") as f_in:
        event = json.load(f_in)
        with codecs.open(basename + ".html", "w", "utf-8") as f_out:
            f_out.write(local_handler(event, {'basename': basename}))


if __name__ == '__main__':
    local_dump('event1')
    local_dump('event2')
    local_dump('event3')
    local_dump('recovery')
