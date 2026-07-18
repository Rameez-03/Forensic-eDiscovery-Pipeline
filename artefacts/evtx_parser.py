"""
Module 7 - Windows Artefact Parser / DFIR.

Parses Windows Event Log files (.evtx) and extracts forensically significant
events into a structured timeline. This module operates independently of
the email pipeline — it represents the DFIR (Digital Forensics and Incident
Response) track, applying the same structured approach to Windows system
artefacts rather than email.

Key event IDs parsed:
  4624 — Successful logon
  4625 — Failed logon attempt
  4634 — Logoff
  4648 — Logon using explicit credentials (runas, lateral movement indicator)
  4688 — New process created
  4698 — Scheduled task created (persistence mechanism)
  7045 — New service installed (persistence mechanism)

Usage from the command line:
    python -m artefacts.evtx_parser --path <path-to-evtx-file-or-directory>

Example — point at the Windows Security log directly:
    python -m artefacts.evtx_parser --path C:/Windows/System32/winevt/Logs/Security.evtx

Output is written to artefacts/forensic_timeline.json.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TIMELINE_PATH = REPO_ROOT / "artefacts" / "forensic_timeline.json"

SIGNIFICANT_EVENT_IDS = {
    4624: "Successful Logon",
    4625: "Failed Logon",
    4634: "Logoff",
    4648: "Logon with Explicit Credentials",
    4688: "Process Created",
    4698: "Scheduled Task Created",
    7045: "Service Installed",
}


def _get_xml_value(xml: str, tag: str) -> str:
    """Extract the text content of the first occurrence of <tag>...</tag>."""
    open_tag = f"<{tag}>"
    close_tag = f"</{tag}>"
    if open_tag in xml:
        return xml.split(open_tag)[1].split(close_tag)[0].strip()
    return ""


def _get_data_name(xml: str, name: str) -> str:
    """Extract a value from <Data Name="name">value</Data> elements."""
    marker = f'Name="{name}">'
    if marker in xml:
        return xml.split(marker)[1].split("<")[0].strip()
    return ""


def _extract_event_fields(xml: str, event_id: int) -> dict:
    """Extract event-type-specific fields from the XML record."""
    if event_id in (4624, 4625, 4648):
        return {
            "subject_user": _get_data_name(xml, "SubjectUserName"),
            "target_user": _get_data_name(xml, "TargetUserName"),
            "logon_type": _get_data_name(xml, "LogonType"),
            "ip_address": _get_data_name(xml, "IpAddress"),
            "workstation": _get_data_name(xml, "WorkstationName"),
        }
    if event_id == 4634:
        return {
            "target_user": _get_data_name(xml, "TargetUserName"),
            "logon_type": _get_data_name(xml, "LogonType"),
        }
    if event_id == 4688:
        return {
            "new_process": _get_data_name(xml, "NewProcessName"),
            "parent_process": _get_data_name(xml, "ParentProcessName"),
            "creator_user": _get_data_name(xml, "SubjectUserName"),
            "command_line": _get_data_name(xml, "CommandLine"),
        }
    if event_id == 4698:
        return {
            "task_name": _get_data_name(xml, "TaskName"),
            "subject_user": _get_data_name(xml, "SubjectUserName"),
        }
    if event_id == 7045:
        return {
            "service_name": _get_data_name(xml, "ServiceName"),
            "image_path": _get_data_name(xml, "ImagePath"),
            "service_type": _get_data_name(xml, "ServiceType"),
            "start_type": _get_data_name(xml, "StartType"),
        }
    return {}


def parse_evtx_file(evtx_path: Path) -> list[dict]:
    """Parse a single .evtx file and return a list of significant event dicts."""
    try:
        from Evtx.Evtx import Evtx
    except ImportError:
        raise RuntimeError(
            "python-evtx is not installed. Run: pip install python-evtx"
        )

    events = []

    with Evtx(str(evtx_path)) as log:
        for record in log.records():
            try:
                xml = record.xml()

                if "<EventID>" not in xml:
                    continue
                try:
                    event_id = int(_get_xml_value(xml, "EventID"))
                except ValueError:
                    continue

                if event_id not in SIGNIFICANT_EVENT_IDS:
                    continue

                timestamp = ""
                if 'TimeCreated SystemTime="' in xml:
                    timestamp = xml.split('TimeCreated SystemTime="')[1].split('"')[0]

                events.append({
                    "timestamp": timestamp,
                    "event_id": event_id,
                    "event_type": SIGNIFICANT_EVENT_IDS[event_id],
                    "computer": _get_xml_value(xml, "Computer"),
                    "source_file": evtx_path.name,
                    **_extract_event_fields(xml, event_id),
                })
            except Exception:
                continue

    return events


def write_timeline(events: list[dict]) -> None:
    events_sorted = sorted(events, key=lambda e: e.get("timestamp") or "")
    TIMELINE_PATH.write_text(
        json.dumps(events_sorted, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def print_summary(events: list[dict], source: Path) -> None:
    type_counts = Counter(e["event_type"] for e in events)
    timestamps = [e["timestamp"] for e in events if e.get("timestamp")]

    print("=" * 60)
    print("EVTX PARSER SUMMARY")
    print("=" * 60)
    print(f"Source:                         {source}")
    print(f"Total significant events:       {len(events)}")
    print()
    print("By event type:")
    for event_type, count in type_counts.most_common():
        print(f"  {event_type:<35} {count}")
    if timestamps:
        print()
        print(f"Timeline: {min(timestamps)[:19]}  to  {max(timestamps)[:19]}")
    print()
    print(f"Timeline written to: artefacts/forensic_timeline.json")
    print("=" * 60)


def run_evtx_parser(path: Path = None) -> None:
    if path is None:
        default = Path("C:/Windows/System32/winevt/Logs/Security.evtx")
        if default.exists():
            path = default
        else:
            print("No .evtx path provided.")
            print("Usage: python -m artefacts.evtx_parser --path <file.evtx>")
            print("Example: python -m artefacts.evtx_parser --path C:/Windows/System32/winevt/Logs/Security.evtx")
            return

    path = Path(path)
    evtx_files = list(path.glob("*.evtx")) if path.is_dir() else [path]

    if not evtx_files:
        print(f"No .evtx files found at {path}")
        return

    all_events: list[dict] = []
    for evtx_file in evtx_files:
        print(f"Parsing {evtx_file.name}...")
        all_events.extend(parse_evtx_file(evtx_file))

    write_timeline(all_events)
    print_summary(all_events, path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Parse Windows .evtx event logs into a forensic timeline."
    )
    parser.add_argument("--path", type=Path, help="Path to .evtx file or directory")
    args = parser.parse_args()
    run_evtx_parser(args.path)
