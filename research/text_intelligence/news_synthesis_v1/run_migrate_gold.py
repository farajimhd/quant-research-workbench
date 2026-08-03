from research.text_intelligence.news_synthesis_v1.migration import default_config, run_migration


def main() -> int:
    manifest = run_migration(default_config())
    print(
        f"NEWS SYNTHESIS V1 DRAFT MIGRATION | records={manifest['records']:,} "
        f"statuses={manifest['status_counts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
