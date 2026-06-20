package testdata;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class SpringMvcController {

    @GetMapping("/users")
    public String getUsers() { return "users"; }

    @PostMapping
    public String create() { return "created"; }

    @PutMapping("/{id}")
    public String update(@PathVariable String id) { return id; }

    @DeleteMapping
    public String delete() { return "deleted"; }

    @PatchMapping
    public String patch() { return "patched"; }

    @RequestMapping(value = "/mixed", method = {RequestMethod.GET, RequestMethod.POST})
    public String mixed() { return "mixed"; }

    @GetMapping
    public String listNoPath() { return "list"; }

    @GetMapping(path = "/alt")
    public String altPath() { return "alt"; }
}
