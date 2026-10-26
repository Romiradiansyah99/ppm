"""Corpus reporting: counts, duplicates, licence mix, exclusions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ppm.corpus.manifest import CorpusReport, resolve_corpus, scan_file_hash


@dataclass(frozen=True)
class DuplicateGroup:
    hash: str
    paths: list[str]


def find_duplicates(report: CorpusReport, compute_hash: bool = True) -> list[DuplicateGroup]:
    if not compute_hash:
        return []
    groups: dict[str, list[str]] = defaultdict(list)
    for entry in report.indexable:
        try:
            groups[scan_file_hash(entry.path)].append(str(entry.path))
        except OSError:
            continue
    return [
        DuplicateGroup(hash=digest, paths=sorted(paths))
        for digest, paths in sorted(groups.items())
        if len(paths) > 1
    ]


def format_report(report: CorpusReport, duplicates: list[DuplicateGroup]) -> str:
    lines: list[str] = []
    lines.append(f"base_dir: {report.base_dir}")
    lines.append(f"entries: {len(report.entries)}  indexable: {len(report.indexable)}  catalog_only: {len(report.catalog_only)}")
    lines.append("")
    lines.append("by collection:")
    for collection, count in report.by_collection().items():
        lines.append(f"  {collection:<14} {count}")
    lines.append("")
    lines.append("by licence:")
    for licence, count in report.by_licence().items():
        lines.append(f"  {licence:<22} {count}")
    lines.append("")
    stage_counts: dict[str, int] = defaultdict(int)
    for entry in report.entries:
        stage_counts[entry.stage] += 1
    lines.append("by stage:")
    for stage, count in sorted(stage_counts.items()):
        lines.append(f"  {stage:<16} {count}")

    if duplicates:
        lines.append("")
        lines.append(f"exact duplicates: {len(duplicates)} groups")
        for group in duplicates:
            lines.append(f"  {group.hash[:12]}  {len(group.paths)} copies")
            for path in group.paths:
                lines.append(f"    {path}")

    if report.excluded:
        lines.append("")
        reasons: dict[str, int] = defaultdict(int)
        for exclusion in report.excluded:
            reasons[exclusion.reason.split(":")[0]] += 1
        lines.append(f"excluded: {len(report.excluded)}")
        for reason, count in sorted(reasons.items()):
            lines.append(f"  {reason:<24} {count}")

    if report.warnings:
        lines.append("")
        lines.append("warnings:")
        for warning in report.warnings:
            lines.append(f"  {warning}")
    return "\n".join(lines)


def build_report(manifest_path: str | Path, compute_hash: bool = True) -> tuple[CorpusReport, list[DuplicateGroup]]:
    report = resolve_corpus(manifest_path)
    return report, find_duplicates(report, compute_hash=compute_hash)
