#!/usr/bin/env bash
# The Features board (GitHub Project 1) from the command line, for people and agents.
#
#   scripts/board.sh new <idea|bug|enhancement> "Area: title" [--area A] [--prio P0|P1|P2] [--parent N] < body.md
#   scripts/board.sh status <issue> <idea|define|ready|doing|done|dropped>
#   scripts/board.sh area <issue> <Lessons|Draft|Assistant|Import|LaTeX|PDF|Publishing|Deploy|UI|Docs>
#   scripts/board.sh prio <issue> <P0|P1|P2>
#   scripts/board.sh sub <parent> <child>        make <child> a sub-issue of <parent>
#   scripts/board.sh list [status]               open items: number, status, kind, area, priority, title
#
# `new` prints the issue number. Needs `gh` logged in with the `project` and `repo` scopes, and `jq`.
set -euo pipefail

REPO=enricocovili/lecta
OWNER=enricocovili
NUMBER=1
PROJECT=PVT_kwHOBB8I684BlkEZ

die() { echo "board: $*" >&2; exit 1; }

status_name() {
  case $1 in
    idea) echo "💡 Idea" ;; define) echo "🔍 Da definire" ;; ready) echo "✅ Pronta" ;;
    doing) echo "🚧 In corso" ;; done) echo "✔️ Fatta" ;; dropped) echo "🗑️ Scartata" ;;
    *) die "unknown status '$1' (idea, define, ready, doing, done, dropped)" ;;
  esac
}

prio_name() {
  case $1 in
    P0) echo "P0 · subito" ;; P1) echo "P1 · prossima" ;; P2) echo "P2 · prima o poi" ;;
    *) die "unknown priority '$1' (P0, P1, P2)" ;;
  esac
}

# set_field <item id> <field name> <option name>
set_field() {
  local ids
  ids=$(gh project field-list $NUMBER --owner $OWNER --format json --jq \
    ".fields[] | select(.name == \"$2\") | \"\(.id) \(.options[] | select(.name == \"$3\") | .id)\"")
  [ -n "$ids" ] || die "no option '$3' in field '$2'"
  gh project item-edit --project-id $PROJECT --id "$1" --field-id "${ids% *}" \
    --single-select-option-id "${ids#* }" >/dev/null
}

# item <issue number>: the issue's board item id (adds the issue to the board if needed)
item() {
  gh project item-add $NUMBER --owner $OWNER --url "https://github.com/$REPO/issues/$1" --format json --jq .id
}

issue_id() { gh issue view "$1" -R $REPO --json id --jq .id; }

sub() {
  gh api graphql -f parent="$(issue_id "$1")" -f child="$(issue_id "$2")" -f query='
    mutation($parent: ID!, $child: ID!) { addSubIssue(input: {issueId: $parent, subIssueId: $child}) { issue { id } } }' \
    >/dev/null
}

cmd=${1:-}; shift || true
case $cmd in
  new)
    [ $# -ge 2 ] || die "usage: new <idea|bug|enhancement> \"title\" [--area A] [--prio P] [--parent N] < body"
    kind=$1 title=$2; shift 2
    case $kind in idea) status=idea ;; bug|enhancement) status=ready ;; *) die "unknown kind '$kind'" ;; esac
    area= prio= parent=
    while [ $# -gt 0 ]; do
      case $1 in
        --area) area=$2; shift 2 ;; --prio) prio=$2; shift 2 ;; --parent) parent=$2; shift 2 ;;
        *) die "unknown option '$1'" ;;
      esac
    done
    url=$(gh issue create -R $REPO --title "$title" --label "$kind" --body-file -)
    n=${url##*/}
    it=$(item "$n")
    set_field "$it" Status "$(status_name $status)"
    [ -z "$area" ] || set_field "$it" Area "$area"
    [ -z "$prio" ] || set_field "$it" "Priorità" "$(prio_name "$prio")"
    [ -z "$parent" ] || sub "$parent" "$n"
    echo "$n"
    ;;
  status) [ $# -eq 2 ] || die "usage: status <issue> <status>"; set_field "$(item "$1")" Status "$(status_name "$2")" ;;
  area)   [ $# -eq 2 ] || die "usage: area <issue> <area>"; set_field "$(item "$1")" Area "$2" ;;
  prio)   [ $# -eq 2 ] || die "usage: prio <issue> <P0|P1|P2>"; set_field "$(item "$1")" "Priorità" "$(prio_name "$2")" ;;
  sub)    [ $# -eq 2 ] || die "usage: sub <parent> <child>"; sub "$1" "$2" ;;
  list)
    want=; [ $# -eq 0 ] || want=$(status_name "$1")
    gh project item-list $NUMBER --owner $OWNER --limit 500 --format json | jq -r --arg want "$want" '
      .items[] | select(.content.state != "CLOSED") | select($want == "" or .status == $want)
      | [(.content.number // "draft" | tostring), (.status // "-"),
         ([.labels[]? | select(. == "idea" or . == "bug" or . == "enhancement")] | join(",") | if . == "" then "-" else . end),
         (.area // "-"), (.["priorità"] // "-"), .title] | @tsv'
    ;;
  *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; [ -z "$cmd" ] || exit 1 ;;
esac
