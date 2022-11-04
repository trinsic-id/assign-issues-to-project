import logging
import os
from datetime import datetime, timedelta
from typing import Tuple, Dict

from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport


def strip_empty(l: list) -> list:
    return [x for x in l if x]


class GithubAutomation:
    """Automate certain GitHub operations for our team"""

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
        project_items = strip_empty(self.get_project_items())
        for item in project_items:
            status, field_id, status_options = self.get_project_status(item)
            if (
                status
                and status.lower() != "done"
                and item['content']
                and item["content"]["state"].lower() == "closed"
            ):
                logging.info(f"Marking {item} done")
                done_id = [
                    option["id"]
                    for option in status_options
                    if option["name"].lower() == "done"
                ][0]
                self.update_project_v2_item(
                    self.project_id, item["id"], field_id, done_id
                )

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
        field_values = strip_empty(node["fieldValues"]["nodes"])
        if not field_values:
            return "", "", {}
        status_field: dict = [
            field
            for field in field_values
            if field and field["field"]["name"] == "Status"
        ][0]

        return (
            status_field["name"],
            status_field["field"]["id"],
            status_field["field"]["options"],
        )

    def assign_issues_to_project(self):
        # Get PRs by repository
        for repository in self.repository_names:
            for issue in self.get_repository_issues(repository):
                self.add_to_project(self.project_id, issue["id"])

    @staticmethod
    def get_graphql(file_name: str) -> str:
        with open(os.path.join(os.path.dirname(__file__), file_name), "r") as fid:
            return "\n".join(fid.readlines())

    def get_project_data(self):
        query = gql(GithubAutomation.get_graphql("get_project_id.graphql"))
        result = self.client.execute(
            query,
            variable_values={
                "number": GithubAutomation.platform_project_id,
                "org": GithubAutomation.org_name,
            },
        )
        self.project_id = result["organization"]["projectV2"]["id"]

    def add_to_project(self, project_id: str, item_id: str):
        query = gql(GithubAutomation.get_graphql("add_to_project.graphql"))
        self.client.execute(
            query, variable_values={"project": project_id, "item_id": item_id}
        )

    def remove_from_project(self, project_id: str, item_id: str):
        query = gql(
            """
        """
        )
        self.client.execute(
            query, variable_values={"project": project_id, "item_id": item_id}
        )

    def update_project_v2_item(
        self, project_id: str, item_id: str, field_id: str, field_value: str
    ):
        query = gql(GithubAutomation.get_graphql("update_project_v2_item.graphql"))
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
        query = gql(GithubAutomation.get_graphql("get_project_items.graphql"))
        project_items = []
        page_info = {"hasNextPage": True}
        end_cursor = ""
        # Prevent infinite loop
        while page_info["hasNextPage"]:
            result = self.client.execute(
                query,
                variable_values={
                    "org": self.org_name,
                    "project_number": self.platform_project_id,
                    "page_size": page_size,
                    "after": end_cursor,
                },
            )
            new_items = result["organization"]["projectV2"]["items"]
            page_info = new_items["pageInfo"]
            end_cursor = page_info["endCursor"]
            project_items.extend(new_items["nodes"])
        return project_items

    def get_repository_issues(self, repo_name: str, update_since: datetime = None):
        update_time_str = (update_since or self.update_since).strftime(
            "%Y-%m-%dT00:00:00Z"
        )
        page_size = 100

        logging.info(
            f"Scanning repository={self.org_name}/{repo_name} for issues since {update_time_str}..."
        )

        query = gql(GithubAutomation.get_graphql("get_repository_issues.graphql"))
        result = self.client.execute(
            query,
            variable_values={
                "org": self.org_name,
                "repo_name": repo_name,
                "page_size": page_size,
                "update_date": update_time_str,
            },
        )
        issues = []
        new_issues = result["organization"]["repository"]["issues"]
        page_info = new_issues["pageInfo"]
        issues.extend(new_issues["nodes"])
        # Prevent infinite loop
        while len(new_issues) >= page_size:
            query = gql(
                GithubAutomation.get_graphql("get_repository_issues_paginated.graphql")
            )
            result = self.client.execute(
                query,
                variable_values={
                    "org": self.org_name,
                    "repo_name": repo_name,
                    "cursor": "",
                    "page_size": page_size,
                    "update_date": update_time_str,
                    "after": page_info["endCursor"],
                },
            )
            new_issues = result["organization"]["repository"]["issues"]
            page_info = new_issues["pageInfo"]
            issues.extend(new_issues["nodes"])
        return issues


if __name__ == "__main__":
    automation = GithubAutomation()
    automation.mark_done_anything_closed()
    automation.assign_issues_to_project()
