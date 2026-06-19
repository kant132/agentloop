package com.example;

import java.util.ArrayList;
import java.util.List;

public class SampleService {

    private List<String> items = new ArrayList<>();

    public void addItem(String item) {
        items.add(item);
        System.out.println("Added: " + item);
    }

    public int countItems() {
        return items.size();
    }

    @Override
    public void processAll(List<String> input) {
        for (String s : input) {
            if (s != null && s.length() > 0) {
                addItem(s.toUpperCase());
            }
        }
        System.out.println("Processed " + input.size() + " items");
    }

    private String concat(String a, String b) {
        return a.concat(b);
    }
}
