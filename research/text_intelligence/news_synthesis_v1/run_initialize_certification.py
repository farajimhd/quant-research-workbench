from research.text_intelligence.news_synthesis_v1.certification import (
    default_certification_config,
    initialize_workspace,
)


def main() -> int:
    manifest = initialize_workspace(default_certification_config())
    print(
        f"NEWS SYNTHESIS V1 CERTIFICATION | review_packets={manifest['review_packets']:,} "
        f"certified={manifest['certified']:,} pending={manifest['pending']:,}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
