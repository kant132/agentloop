package com.javaparsextract.spi;

/**
 * 路由提取结果记录。
 *
 * 9 个字段与原始 Map 输出完全一致，保证 JSON 格式零回归。
 * toMap() 返回向后兼容的 LinkedHashMap。
 */
public record RouteResult(
        String classFqn,
        String classBasePath,
        String methodFqn,
        String methodName,
        String fullUrl,
        java.util.List<String> httpMethods,
        String annotation,
        String file,
        int startLine) {

    /** 转为向后兼容的 Map（用于 JSON 序列化）。 */
    public java.util.Map<String, Object> toMap() {
        java.util.Map<String, Object> map = new java.util.LinkedHashMap<>();
        map.put("class_fqn", classFqn);
        map.put("class_base_path", classBasePath);
        map.put("method_fqn", methodFqn);
        map.put("method_name", methodName);
        map.put("full_url", fullUrl);
        map.put("http_methods", httpMethods);
        map.put("annotation", annotation);
        map.put("file", file);
        map.put("start_line", startLine);
        return map;
    }

    /** 设置文件路径（用于编排层注入文件路径）。 */
    public RouteResult withFile(String filePath) {
        return new RouteResult(classFqn, classBasePath, methodFqn, methodName,
                fullUrl, httpMethods, annotation, filePath, startLine);
    }
}
