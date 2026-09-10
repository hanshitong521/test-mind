package com.example.demo;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import jakarta.validation.constraints.NotNull;

public class SampleController {
    @PostMapping("/api/items")
    public void create(@RequestBody @NotNull ItemReq req) {}
}
