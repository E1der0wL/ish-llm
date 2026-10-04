"""이미지를 등록하고 명시된 OCR 또는 VLM을 호출한다. 라이브러리 설정 기본값을 만들지 않는다."""

import argparse
import asyncio
import json
from pathlib import Path

from llm.llm import LargeLanguageModel, ProjectConfig, VisionComponent


async def run(args):
    config = ProjectConfig(json.loads(args.config.read_text(encoding="utf-8")))
    async with LargeLanguageModel(args.workspace, components=[VisionComponent()]) as backend:
        project = await backend.projects.acreate("Vision check", components=["vision"], config=config)
        vision = await project.components.aget("vision")
        image = await vision.aimport_image(args.image, title=args.image.name)
        if args.operations is not None:
            image = await vision.apreprocess(image["id"], operations=json.loads(args.operations.read_text(encoding="utf-8")))
        if args.mode == "ocr":
            result = await vision.aocr(image["id"])
        else:
            result = await vision.aanalyze(image["id"], prompt=args.prompt)
        print("Project:", project.paths.root)
        print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--mode", choices=("ocr", "analysis"), required=True)
    parser.add_argument("--operations", type=Path, help="Explicit preprocessing operation JSON array")
    parser.add_argument("--prompt", help="Required only for analysis")
    args = parser.parse_args()
    if args.mode == "analysis" and not args.prompt:
        parser.error("--mode analysis requires --prompt")
    if args.mode == "ocr" and args.prompt is not None:
        parser.error("--prompt is only used for analysis")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
