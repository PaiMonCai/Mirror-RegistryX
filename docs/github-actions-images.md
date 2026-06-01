# GitHub Actions 镜像构建

本项目提供测试版和正式版分离的 GitHub Actions 镜像构建流程，镜像默认发布到 GHCR。

## 测试版镜像：Dev Images

Workflow：`.github/workflows/dev-images.yml`

触发方式：

- 定时触发：每天 02:17 北京时间运行一次
- 手动触发：在 GitHub Actions 页面运行 `Dev Images`，可指定 `image_tag`

默认镜像：

- `ghcr.io/paimoncai/mirror-registryx-panel:<image_tag>`
- `ghcr.io/paimoncai/mirror-registryx-panel:dev-<sha>`
- `ghcr.io/paimoncai/mirror-registryx-sync:<image_tag>`
- `ghcr.io/paimoncai/mirror-registryx-sync:dev-<sha>`

说明：测试版不会因为推送 `main` 自动发布，也不会覆盖 `latest`。

## 正式版镜像：Release Images

Workflow：`.github/workflows/release-images.yml`

触发方式：推送 `v*` tag，例如：

```bash
git tag v0.1.0
git push github v0.1.0
```

正式镜像：

- `ghcr.io/paimoncai/mirror-registryx-panel:v0.1.0`
- `ghcr.io/paimoncai/mirror-registryx-panel:latest`
- `ghcr.io/paimoncai/mirror-registryx-sync:v0.1.0`
- `ghcr.io/paimoncai/mirror-registryx-sync:latest`

## GitHub 仓库设置

1. 将代码推送到 GitHub 仓库。
2. 确认仓库开启 Actions：`Settings -> Actions -> General`。
3. 确认 workflow token 允许写 package：`Settings -> Actions -> General -> Workflow permissions -> Read and write permissions`。
4. 测试版在 `Actions -> Dev Images -> Run workflow` 手动运行。
5. 正式版通过推送 `v*` tag 发布。

## 注意

当前镜像命名空间固定为 `paimoncai`。如果 GitHub 用户名或组织名不是这个值，需要同步修改：

- `.github/workflows/dev-images.yml` 中的 `IMAGE_NAMESPACE`
- `.github/workflows/release-images.yml` 中的 `IMAGE_NAMESPACE`
