# pinboard-to-bluesky

Gateway pinboard.in posts to BlueSky -- as used on https://bsky.app/profile/jmason.ie .

## How To Use

Create a new BlueSky user account (it's good manners to include a line in the bio indicating that
it's a bot account).  Edit the script and fill in the username and password for 'bsky_user'
and 'bsky_password'.

Fill in the appropriate RSS feed address for your Pinboard account; changing the "u:jm"
part to match your username should be all that is needed.

Once this is done, run "./gateway.py" as often as necessary; it'll maintain a sqlite
database to track which URLs have been previously posted.

