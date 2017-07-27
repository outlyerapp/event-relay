#!/bin/bash

cwd=`pwd`

rm -f event-relay.zip
zip event-relay.zip relay.py timeline.py email.html email.txt
cd $HOME/.virtualenvs/event-relay/lib/python3.6/site-packages
find . \( -path ./boto\* -o -path ./pip\* -o -path ./setuptools\* -o -path ./docutils\* \) -prune -o -print | zip -u9 $cwd/event-relay.zip -@

cd $cwd
aws s3 cp event-relay.zip s3://event-relay.deploy
aws lambda update-function-code --function-name event_relay_func --s3-bucket event-relay.deploy --s3-key event-relay.zip
