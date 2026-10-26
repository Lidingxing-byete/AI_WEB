/// <reference types="vite/client" />

// 说明：本项目用 import.meta.env.BASE_URL 计算子路径前缀（部署到 /xz/ 时），
// 需要 vite 的客户端类型声明，否则 tsc 会报 "Property 'env' does not exist"。
