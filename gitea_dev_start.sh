#!/usr/bin/env bash

cmd="$0"
FROM_SCRATCH=false

ENV_FILE=".env.dev.local"

GITEA_URL=http://localhost:3000
GITEA_TOKEN=

write_env_dev() {
  echo "GITEA_URL=$GITEA_URL" > $ENV_FILE
  echo "GITEA_TOKEN=$GITEA_TOKEN" >> $ENV_FILE
}

usage() {
  echo
  echo "Initialize gitea server, return token of admin"
  echo
  echo "Usage: $cmd"
  echo " -f - restart from scratch"
  echo " -h - show usage (this)"
}

while getopts "fh" opt; do
  case $opt in
    "f") FROM_SCRATCH=true ;;
    *) usage; exit 0 ;;
  esac
done

shift $((OPTIND-1))

if $FROM_SCRATCH; then
  write_env_dev
  docker-compose -f docker-compose.gitea.yml down
  podman volume rm gitea-mcp-server_gitea-data
fi

docker-compose -f docker-compose.gitea.yml up -d


max_tries=10

while (( max_tries-- > 0 )); do
  status=$(podman inspect --format {{.State.Health.Status}} gitea-test)
  [[ $status == "healthy" ]] && max_tries=0
  [[ $status == "starting" ]] && sleep 5 && echo -en "\033[2K\rgitea: $status ($max_tries)"
done

[[ $status != "healthy" ]] && { echo "gitea not healthy: $status"; exit 1; }

# create admin username: test-user
# store its token in $ENV_FILE
result=$(podman exec -i -u git gitea-test /bin/bash <<- OEF
/usr/local/bin/gitea admin user create  \
  --password pass --admin  \
  --fullname "Gitea Admin"  \
  --username test-user \
  --access-token \
  --email 'admin@local' 2>/dev/null
exit
OEF
)

GITEA_TOKEN=$(sed -n '/Access token/ s/^.*\.\.\. //p' <<< $result)
if [[ ! -z $GITEA_TOKEN ]]; then
  sed -i 's/^GITEA_TOKEN=.*/GITEA_TOKEN='$GITEA_TOKEN'/' $ENV_FILE
fi

# test token
result=$(source $ENV_FILE; curl -sL -H "Authorization: Bearer $GITEA_TOKEN"  $GITEA_URL/api/v1/user 2>/dev/null)

[[ $(jq .username <<<$result) == "test-user" ]] && { echo "token invalid, check $ENV_FILE"; exit 1; }
echo "token for test-user works. Stored in $ENV_FILE"

#Access token was successfully created... 5e80f15f61e5ba898f5fc76067bd541155e71147
