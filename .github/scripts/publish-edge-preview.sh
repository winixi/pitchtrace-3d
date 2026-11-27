#!/usr/bin/env bash
set -euo pipefail

cd "${1:?请指定已测试的附件目录}"
tag=${GITHUB_REF_NAME:?缺少标签名}
archive="pitchtrace-${tag}-macos-arm64.zip"
checksum="$archive.sha256"
sha256sum "$archive" | cmp - "$checksum"

commit=$(git -C "$GITHUB_WORKSPACE" rev-parse HEAD)
verify_tag() {
  local remote
  remote=$(gh api "repos/$GH_REPO/commits/tags/$tag" --jq .sha)
  if [[ "$remote" != "$commit" ]]; then
    printf '标签 %s 当前指向 %s，本次构建使用 %s。\n' "$tag" "$remote" "$commit" >&2
    exit 1
  fi
}
verify_tag

if release=$(gh release view "$tag" --json isDraft,isPrerelease,assets 2>&1); then
  if [[ $(jq -r '.isDraft' <<< "$release") == false ]]; then
    if [[ $(jq -r '.isPrerelease' <<< "$release") != true ]]; then
      printf '%s 已是正式版本，无法作为预发布上传。\n' "$tag" >&2
      exit 1
    fi

    # 已公开的附件以原件为准。重建的 ZIP 可能有不同时间戳。
    published=$(mktemp -d)
    trap 'rm -rf -- "$published"' EXIT
    missing=()
    for file in "$archive" "$checksum"; do
      if jq -e --arg name "$file" 'any(.assets[]; .name == $name)' <<< "$release" >/dev/null; then
        gh release download "$tag" --pattern "$file" --dir "$published"
      else
        cp "$file" "$published/"
        missing+=("$file")
      fi
    done
    cd "$published"
    # 只在新旧附件可组成同一对时补传，保留已经发布的下载内容。
    sha256sum "$archive" | cmp - "$checksum"
    for file in "${missing[@]}"; do
      gh release upload "$tag" "$file"
    done
    exit 0
  fi
elif [[ "$release" == 'release not found' ]]; then
  gh release create "$tag" --verify-tag --draft --prerelease --latest=false \
    --title "PitchTrace Edge ${tag#edge-} · 离线 CLI 预览版" --notes-file "$GITHUB_WORKSPACE/edge/preview.md"
else
  printf '%s\n' "$release" >&2
  exit 1
fi

# 上传失败时保留草稿，重跑后使用本次验证过的整套附件。
gh release upload "$tag" "$archive" "$checksum" --clobber
verify_tag
gh release edit "$tag" --verify-tag --draft=false --prerelease --latest=false
