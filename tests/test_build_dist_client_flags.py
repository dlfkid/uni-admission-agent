import scripts.build_dist as build_dist


def test_parse_args_accepts_client_only() -> None:
    args = build_dist.parse_args(["--client-only"])
    assert args.client_only is True


def test_parse_args_accepts_skip_client_build() -> None:
    args = build_dist.parse_args(["--separate-artifacts", "--skip-client-build"])
    assert args.separate_artifacts is True
    assert args.skip_client_build is True

