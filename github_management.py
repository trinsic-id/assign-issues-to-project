import datetime
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Tuple, Dict

from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport


class GithubAutomation:
    """Automate certain github operations for our team"""

    platform_project_id = int(os.getenv("GITHUB_PROJECT_NUMBER", "N/A"))
    api_token = os.getenv("API_GITHUB_TOKEN")
    org_name = os.getenv("GITHUB_ORG_NAME")
    repository_names = [x.strip() for x in os.getenv("GITHUB_REPO_NAMES").split(",")]
    update_since = datetime.now().date() - timedelta(days=10)

    def __init__(self):
        self.project_id = None

        assert len(GithubAutomation.api_token.strip()) > 0
        assert len(GithubAutomation.org_name.strip()) > 0
        assert len(GithubAutomation.repository_names) > 0

        transport = AIOHTTPTransport(url="https://api.github.com/graphql")
        transport.headers = {"Authorization": f"bearer {GithubAutomation.api_token}"}
        self.client = Client(transport=transport, fetch_schema_from_transport=True)

        self.get_project_data()

    def mark_done_anything_closed(self):
        project_items = self.get_project_items()
        for item in project_items:
            node = item["node"]
            try:
                status_id, field_id, status_options = self.get_project_status(node)
                if any(
                    [
                        option["id"] == status_id
                        and option["name"].lower() != "done"
                        and node["content"]["__typename"].lower() == "issue"
                        and node["content"]["state"].lower() == "closed"
                        for option in status_options["options"]
                    ]
                ):
                    logging.info(f"Marking {node} done")
                    done_id = [
                        option["id"]
                        for option in status_options["options"]
                        if option["name"].lower() == "done"
                    ][0]
                    self.update_project_next_item(
                        self.project_id, node["id"], field_id, done_id
                    )
            except Exception as e:
                logging.error(f"Failed on {node}: {e}")
                pass

    @staticmethod
    def get_issue_states(repo_issues: Dict[str, list]) -> Dict[str, str]:
        return dict(
            [
                (issue["node"]["id"], issue["node"]["state"])
                for repo_issues in repo_issues.values()
                for issue in repo_issues
            ]
        )

    @staticmethod
    def get_project_status(node: dict) -> Tuple[str, str, dict]:
        field_values = node["fieldValues"]["edges"]
        status_field: dict = [
            field["node"]
            for field in field_values
            if field["node"]["projectField"]["name"] == "Status"
        ][0]

        return (
            status_field["value"],
            status_field["projectField"]["id"],
            json.loads(status_field["projectField"]["settings"]),
        )

    def assign_issues_to_project(self):
        # Get PRs by repository
        for repository in self.repository_names:
            for issue in self.get_repository_issues(repository):
                self.add_to_project(self.project_id, issue["node"]["id"])

    def get_project_data(self):
        query = gql(
            """
query($org: String!, $number: Int!) {
  organization(login: $org){
    projectNext(number: $number) {
      id
      fields(first:100) {
        nodes {
          id
          name
          settings
        }
      }
    }
  }
}
    """
        )
        result = self.client.execute(
            query,
            variable_values={
                "number": GithubAutomation.platform_project_id,
                "org": GithubAutomation.org_name,
            },
        )
        self.project_id = result["organization"]["projectNext"]["id"]

    def add_to_project(self, project_id: str, item_id: str):
        query = gql(
            """
mutation($project:ID!, $item_id:ID!) {
  addProjectNextItem(input: {projectId: $project, contentId: $item_id}) {
    projectNextItem {
      id
    }
  }
}
        """
        )
        self.client.execute(
            query, variable_values={"project": project_id, "item_id": item_id}
        )

    def remove_from_project(self, project_id: str, item_id: str):
        query = gql(
            """
mutation($project:ID!, $item_id:ID!) {
  deleteProjectNextItem(input: {projectId: $project, contentId: $item_id}) {
    projectNextItem {
      id
    }
  }
}
        """
        )
        self.client.execute(
            query, variable_values={"project": project_id, "item_id": item_id}
        )

    def update_project_next_item(
        self, project_id: str, item_id: str, field_id: str, field_value: str
    ):
        query = gql(
            """
mutation ($project: ID!, $item_id: ID!, $field_id: ID!, $field_value: String!) {
  updateProjectNextItemField(
    input: {projectId: $project, itemId: $item_id, fieldId: $field_id, value: $field_value}
  ) {
    projectNextItem {
      id
    }
  }
}
            """
        )
        self.client.execute(
            query,
            variable_values={
                "project": project_id,
                "item_id": item_id,
                "field_id": field_id,
                "field_value": field_value,
            },
        )

    def get_project_items(self):
        page_size = 100
        query = gql(
            """
query ($org: String!, $project_number: Int!, $page_size: Int!) {
    organization(login: $org) {
        projectNext(number: $project_number) {
            id
            items(first: $page_size) {
                edges {
                    node {
                        fieldValues(first:100){
                            edges {
                                node {
                                    projectField {
                                        id
                                        name
                                        dataType
                                        settings
                                    }
                                    value
                                }
                            }
                        }
                        content {
                            __typename
                            ... on Issue {
                                id
                                state
                            }
                        }
                        id
                        isArchived
                        updatedAt
                        title
                        type
                    }
                    cursor
                }
            }
        }
    }
}
    """
        )
        result = self.client.execute(
            query,
            variable_values={
                "org": self.org_name,
                "project_number": self.platform_project_id,
                "page_size": page_size,
            },
        )
        new_items = result["organization"]["projectNext"]["items"]["edges"]
        project_items = []
        project_items.extend(new_items)
        # Prevent infinite loop
        while len(new_items) >= page_size:
            query = gql(
                """
query ($org: String!, $project_number: Int!, $page_size: Int!, $after: String!) {
    organization(login: $org) {
        projectNext(number: $project_number) {
            id
            items(first: $page_size, after: $after) {
                edges {
                    node {
                        fieldValues(first:100){
                            edges {
                                node {
                                    projectField {
                                        id
                                        name
                                        dataType
                                        settings
                                    }
                                    value
                                }
                            }
                        }
                        content {
                            __typename
                            ... on Issue {
                                id
                                state
                            }
                        }
                        id
                        isArchived
                        updatedAt
                        title
                        type
                    }
                    cursor
                }
            }
        }
    }
}
        """
            )
            result = self.client.execute(
                query,
                variable_values={
                    "org": self.org_name,
                    "project_number": self.platform_project_id,
                    "page_size": page_size,
                    "after": new_items[-1]["cursor"],
                },
            )
            new_items = result["organization"]["projectNext"]["items"]["edges"]
            project_items.extend(new_items)
        return project_items

    def get_repository_issues(self, repo_name: str, update_since: datetime = None):
        update_time_str = (update_since or self.update_since).strftime(
            "%Y-%m-%dT00:00:00Z"
        )
        page_size = 100

        logging.info(
            f"Scanning repository={self.org_name}/{repo_name} for issues since {update_time_str}..."
        )

        query = gql(
            """
query($org: String!, $repo_name: String!, $page_size: Int!, $update_date: DateTime!) {
  organization(login: $org) {
    repository(name: $repo_name) {
      id
       issues(first: $page_size, filterBy: {since: $update_date}) {
        edges {
          node {
            id
            title
            number
            createdAt
            state
          }
          cursor
        }
      }
    }
  }
}
    """
        )
        result = self.client.execute(
            query,
            variable_values={
                "org": self.org_name,
                "repo_name": repo_name,
                "cursor": "",
                "page_size": page_size,
                "update_date": update_time_str,
            },
        )
        new_issues = result["organization"]["repository"]["issues"]["edges"]
        issues = []
        issues.extend(new_issues)
        # Prevent infinite loop
        while len(new_issues) >= page_size:
            query = gql(
                """
query ($org: String!, $repo_name: String!, $page_size: Int!, $update_date: DateTime!, $after: String!) {
  organization(login: $org) {
    repository(name: $repo_name) {
      id
       issues(first: $page_size, after: $after, filterBy: {since: $update_date}) {
        edges {
          node {
            id
            title
            number
            createdAt
            state
          }
          cursor
        }
      }
    }
  }
}
        """
            )
            result = self.client.execute(
                query,
                variable_values={
                    "org": self.org_name,
                    "repo_name": repo_name,
                    "cursor": "",
                    "page_size": page_size,
                    "update_date": update_time_str,
                    "after": new_issues[-1]["cursor"],
                },
            )
            new_issues = result["organization"]["repository"]["issues"]["edges"]
            issues.extend(new_issues)
        return issues


if __name__ == "__main__":
    automation = GithubAutomation()
    automation.mark_done_anything_closed()
    automation.assign_issues_to_project()
