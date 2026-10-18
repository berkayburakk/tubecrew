# YouTube API access: one-time setup

You use your own Google Cloud project and your own OAuth client. Nothing goes through tubecrew or any shared key,
and the login tokens stay on your computer in `~/.tubecrew/`. It takes about ten minutes.

## 1. Create a Google Cloud project

1. Open <https://console.cloud.google.com/> and sign in with the Google account that owns or manages the channel.
2. Click the project picker in the top bar, then **New project**. Any name works (for example `my-youtube-tools`).
   Create it and make sure it is selected.

## 2. Turn on two APIs

Go to **APIs & Services > Library**. Search for **YouTube Data API v3** and click **Enable**. Do the same for
**YouTube Analytics API**.

## 3. Set up the consent screen

Go to **APIs & Services > OAuth consent screen** (newer consoles call it **Google Auth Platform**).

- User type: **External**. Give the app a name and your own e-mail as the contact.
- Leave the publishing status on **Testing** and add the Google account you will log in with as a **test user**.
- In Testing mode Google expires the login after about 7 days. When a script says the login was revoked or
  expired (`invalid_grant`), run its `--login` command again. To avoid that, set the app to **In production**;
  Google then shows an "app isn't verified" warning at login, which you can pass because the app is your own.

## 4. Create the OAuth client

Go to **APIs & Services > Credentials > Create credentials > OAuth client ID**. Application type: **Desktop app**.
Create it and click **Download JSON**.

## 5. Put the file in place

Save the downloaded file as `~/.tubecrew/client_secret.json`. Keep it out of your video projects and out of git.

- macOS or Linux: `mkdir -p ~/.tubecrew` and move the file there under that name.
- Windows: the folder is `%USERPROFILE%\.tubecrew\` (create it, then move the file in and rename it).

To keep these files somewhere else, set the `TUBECREW_HOME` environment variable to that folder.

## 6. Log in once per script

```bash
pip install google-auth google-auth-oauthlib requests
python skills/upload-private/scripts/upload.py --login         # upload + read-only channel access
python skills/retention-by-scene/scripts/retention.py --login  # read-only analytics
```

A browser opens. Pick the Google account, and the channel if the account has more than one. Each script prints
the channel the token belongs to; copy that id into `upload.json` as `channel_id`. The upload script refuses to
upload when the token's channel and `channel_id` differ.

Tokens are saved as `~/.tubecrew/tokens/<profile>-upload.json` and `<profile>-analytics.json`. A browser opens
only when you pass `--login`; any other time a missing or expired token stops the script with the command to run.

## Several channels

Give each channel its own profile, and use the same `--profile` when you upload or read analytics:

```bash
python upload.py --login --profile cooking
python upload.py <video-project> --upload --profile cooking
```

## Quota

The YouTube Data API has a daily quota per Cloud project. According to
<https://developers.google.com/youtube/v3/determine_quota_cost> (page last updated 2026-10-15, checked
2026-10-18): `videos.insert` has its own bucket of 100 calls per day; all other Data API calls share 10,000 units
per day, where `thumbnails.set` costs 50 units and `channels.list` and `videos.list` cost 1 each. The Analytics
API has a separate quota. Google changes these numbers; check the page if an upload is refused for quota.

## When something goes wrong

- **"Access blocked" or "app has not completed verification"**: the account is not on the test user list (step 3).
- **`invalid_grant`**: the login expired or was revoked. Run `--login` again.
- **Wrong channel**: you logged in with another account or channel. Run `--login` again and pick the right one.
- **Thumbnail HTTP 403**: custom thumbnails need a verified channel (phone verification in YouTube settings).
  The video is uploaded anyway; add the thumbnail in YouTube Studio.
