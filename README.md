# Based on TK Starter App

## Step 6. Tag up a version and switch back to git mode

When you are ready to release, tag up a version in git. Name it for example `v1.0.0`.
Then, switch back to git mode. Toolkit will pick up the tag with the higest number
and use that - your dev area is no longer used by the system.

```
> cd /your/development/sandbox
> ./tank switch_app shot_step tk-maya tk-multi-mynewapp user@remotehost:/path_to/tk-multi-mynewapp.git
```

## Step 7. Push your config changes to the production config

Lastly, push your configuration changes to the Primary production config for the project.

```
> cd /your/development/sandbox
> ./tank push_configuration 
```
