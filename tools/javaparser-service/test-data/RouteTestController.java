package com.example;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/users")
public class RouteTestController {

    @GetMapping("/{id}")
    public String getById(@PathVariable String id) {
        return id;
    }

    @PostMapping
    public String create() {
        return "created";
    }

    @RequestMapping(value = "/search", method = {RequestMethod.GET, RequestMethod.POST})
    public String search() {
        return "search";
    }

    @RequestMapping("/all")
    public String allMethods() {
        return "all";
    }
}
