package com.javaparsextract.spi;

import java.util.List;
import java.util.Map;

/**
 * 极简 JSON 序列化（向后兼容，无外部依赖）。
 */
public final class JsonUtils {

    public static String toJson(List<Map<String, Object>> routes) {
        if (routes.isEmpty()) return "[]";
        StringBuilder sb = new StringBuilder("[\n");
        for (int i = 0; i < routes.size(); i++) {
            sb.append("  ").append(toJsonObj(routes.get(i)));
            if (i < routes.size() - 1) sb.append(",");
            sb.append("\n");
        }
        sb.append("]");
        return sb.toString();
    }

    public static String toJsonObj(Map<String, Object> obj) {
        StringBuilder sb = new StringBuilder("{");
        int i = 0;
        for (Map.Entry<String, Object> entry : obj.entrySet()) {
            if (i++ > 0) sb.append(", ");
            sb.append("\"").append(escapeJson(entry.getKey())).append("\": ");
            Object v = entry.getValue();
            if (v instanceof Number) { sb.append(v); }
            else if (v instanceof List<?> list) {
                sb.append("[");
                for (int j = 0; j < list.size(); j++) {
                    if (j > 0) sb.append(", ");
                    sb.append("\"").append(escapeJson(String.valueOf(list.get(j)))).append("\"");
                }
                sb.append("]");
            } else { sb.append("\"").append(escapeJson(String.valueOf(v))).append("\""); }
        }
        sb.append("}");
        return sb.toString();
    }

    public static String escapeJson(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"")
                .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t");
    }
}
