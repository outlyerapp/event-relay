Using the Event Relay
=====================

 1. [Generate an Outlyer API key][keygen] if you don't already have one.
 2. Add a webhook action to one of your alerts. Set it up as follows:  
    **Webhook URL:** https://relay.outlyer.com/api/v1/relay  
    **Payload format:** JSON body  
    **Extra payload:** 
``` json
{
  "to": [
    {
      "email": "you@domain.com",
      "name": "Your Name"
    }
  ],
  "cc": [ ],
  "bcc": [ ],
  "format": "html",
  "extra_message": "Yo! Something is on fire!",
  "subject": "[Outlyer] Alert '{{ event.rule }}' is now {{ event.status |upper }}",
  "api_key": "xxx"
}
```

## JSON Payload Definition

| Field Name | Meaning |
| ---------- | ------- |
| to | A list of recipients for the email message. Each recipient should be an object with **email** and **name** fields. |
| cc | Recipients to be CC'ed on the email. Same format as **to**. |
| bcc | Recipients to be BCC'ed on the email. Same format as **to**. |
| format | Must be either "html" to send an HTML-formatted email, or "text" to send plain text. |
| subject | Subject line for the email. 
| extra_message | Extra information to include in the email message. [Jinja][jinja2] template substitutions are allowed. | 
| api_key | The API key generated during step 1 above. |

## Coming Soon
 
 - [ ] Configurable email/plain text templates
 - [ ] Muting alerts for specific hosts
 - [ ] Muting for service dependencies


[keygen]: https://app.outlyer.com/#/user-account/api-tokens
[jinja2]: http://jinja.pocoo.org/docs/2.9/templates/
