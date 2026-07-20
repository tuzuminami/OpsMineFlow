from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from opsmineflow_mining import load_events_from_csv

from opsmineflow_api.app import create_export_artifact, create_process_map
from opsmineflow_api.storage import EventStore, ProjectConflictError


class ProjectIsolationTests(unittest.TestCase):
    def _event(self):
        return load_events_from_csv("data/sample/sample_events.csv")[0]

    def test_same_event_id_and_all_scoped_state_are_isolated_between_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = EventStore(db_path=Path(temp_dir) / "opsmineflow.sqlite3")
            project_a = workspace.create_project("Accounts payable")
            project_b = workspace.create_project("Customer support")
            event = self._event()
            event_a = replace(event, activity_raw="AP-only review", activity_normalized="AP-only review")
            event_b = replace(event, activity_raw="Support-only review", activity_normalized="Support-only review")

            store_a = workspace.for_project(project_a.project_id)
            store_b = workspace.for_project(project_b.project_id)
            store_a.replace([event_a], import_source="csv", import_path="accounts.csv")
            store_a.set_label(event.event_id, "AP review")
            store_a.update_settings({"excluded_apps": ["Private Browser"]})
            store_a.set_automation_review("Review invoice", "adopted", "owned by finance")

            store_b.replace([event_b], import_source="csv", import_path="support.csv")
            store_b.set_label(event.event_id, "Support review")
            store_b.update_settings({"excluded_domains": ["internal.example"]})
            store_b.set_automation_review("Review invoice", "rejected", "not a support workflow")

            reopened_a = workspace.for_project(project_a.project_id)
            reopened_b = workspace.for_project(project_b.project_id)
            self.assertEqual([item.event_id for item in reopened_a.snapshot().events], [event.event_id])
            self.assertEqual([item.event_id for item in reopened_b.snapshot().events], [event.event_id])
            self.assertEqual(reopened_a.snapshot().manual_labels[event.event_id], "AP review")
            self.assertEqual(reopened_b.snapshot().manual_labels[event.event_id], "Support review")
            self.assertEqual(reopened_a.get_settings()["excluded_apps"], ["Private Browser"])
            self.assertEqual(reopened_b.get_settings()["excluded_apps"], [])
            self.assertEqual(reopened_a.snapshot().automation_reviews["Review invoice"], "adopted")
            self.assertEqual(reopened_b.snapshot().automation_reviews["Review invoice"], "rejected")
            self.assertEqual(reopened_a.list_import_history()[0]["path"], "accounts.csv")
            self.assertEqual(reopened_b.list_import_history()[0]["path"], "support.csv")
            self.assertEqual(create_process_map(reopened_a)["nodes"][0]["activity"], "AP-only review")
            self.assertEqual(create_process_map(reopened_b)["nodes"][0]["activity"], "Support-only review")
            self.assertIn("AP-only review", str(create_export_artifact("json", reopened_a)["content"]))
            self.assertNotIn("Support-only review", str(create_export_artifact("json", reopened_a)["content"]))

            reopened_a.clear()
            after_clear_b = workspace.for_project(project_b.project_id)
            self.assertEqual(len(after_clear_b.snapshot().events), 1)
            self.assertEqual(after_clear_b.snapshot().events[0].activity_normalized, "Support-only review")
            self.assertEqual(after_clear_b.snapshot().manual_labels[event.event_id], "Support review")
            self.assertEqual(after_clear_b.list_import_history()[0]["path"], "support.csv")

    def test_stale_project_revision_cannot_overwrite_a_newer_project_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = EventStore(db_path=Path(temp_dir) / "opsmineflow.sqlite3")
            project = workspace.create_project("Revenue operations")
            event = self._event()
            initial = workspace.for_project(project.project_id)
            initial.replace([event])
            revision = initial.snapshot().project_revision

            stale = workspace.for_project(project.project_id, expected_revision=revision)
            current = workspace.for_project(project.project_id)
            current.set_label(event.event_id, "current")

            with self.assertRaises(ProjectConflictError):
                stale.set_label(event.event_id, "stale")
            with self.assertRaises(ProjectConflictError):
                workspace.for_project(project.project_id, expected_revision=revision)
            self.assertEqual(workspace.for_project(project.project_id).snapshot().manual_labels[event.event_id], "current")

    def test_project_scope_reuses_the_initialized_schema_without_running_migrations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = EventStore(db_path=Path(temp_dir) / "opsmineflow.sqlite3")
            project = workspace.create_project("Migration-free scope")

            with patch("opsmineflow_api.storage.migrate_database", side_effect=AssertionError("must not migrate per request")):
                scoped = workspace.for_project(project.project_id)

            self.assertEqual(scoped.snapshot().project_id, project.project_id)

    def test_project_delete_requires_an_empty_dataset_and_updates_the_durable_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = EventStore(db_path=Path(temp_dir) / "opsmineflow.sqlite3")
            populated = workspace.create_project("Populated")
            empty = workspace.create_project("Empty")
            workspace.for_project(populated.project_id).replace([self._event()])
            workspace.select_project(empty.project_id)

            with self.assertRaisesRegex(ValueError, "Clear the project's data"):
                workspace.delete_project(populated.project_id)

            replacement = workspace.delete_project(empty.project_id)
            self.assertNotEqual(replacement, empty.project_id)
            self.assertEqual(workspace.active_project_id(), replacement)
            self.assertNotIn(empty.project_id, {project.project_id for project in workspace.list_projects()})


if __name__ == "__main__":
    unittest.main()
