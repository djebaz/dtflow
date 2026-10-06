"""Capture exported screen kits without changing their fixtures or appearance."""

from design_capture import capture_session, images_ready, manifest, parser, ready


def main():
    args = parser(__doc__).parse_args()
    report = {"captures": [], "failures": []}
    try:
        with capture_session(args) as (page, base, diagnostics):
            report.update(diagnostics)
            for name, kit, mode, width, height in [
                ("10-connection", "connection", None, 1600, 1000),
                ("11-generate", "generate", None, 1600, 1000),
                ("12-queues", "queues", None, 1600, 1000),
                ("13-images", "images", None, 1600, 1000),
                ("20-gallery-grid", "images", "Grid", 1280, 900),
                ("20-gallery-list", "images", "List", 1280, 900),
                ("20-gallery-detail", "images", "Detail", 1280, 900),
                ("21-gallery-detail-details", "images", "Detail", 1280, 1000),
            ]:
                try:
                    page.set_viewport_size({"width": width, "height": height})
                    page.goto(f"{base}/ui_kits/{kit}/index.html")
                    ready(page)
                    page.locator("#root > *").first.wait_for(state="visible")
                    if mode:
                        page.get_by_role("button", name=mode, exact=True).click()
                    if name.endswith("details"):
                        page.get_by_role("button", name="Additional details", exact=True).click()
                        page.get_by_role("button", name="Additional details", exact=True, pressed=True).wait_for()
                    page.mouse.move(width - 1, height - 1)
                    image_count = images_ready(page)
                    page.screenshot(path=str(args.out / f"{name}.png"), animations="disabled")
                    report["captures"].append(
                        {
                            "name": name,
                            "viewport": [width, height],
                            "fixture": "kit default",
                            "mode": mode,
                            "decoded_images": image_count,
                        }
                    )
                except Exception as error:
                    report["failures"].append({"name": name, "error": str(error)})
    except Exception as error:
        report["failures"].append({"setup": str(error)})
    if (args.design_system / "_ds_bundle.js").exists():
        manifest(args, report)
    for failure in report["failures"]:
        print(failure)
    print(f"Captured {len(report['captures'])} screens; {len(report['failures'])} failures")
    return int(bool(report["failures"] or report.get("errors")))


if __name__ == "__main__":
    raise SystemExit(main())
