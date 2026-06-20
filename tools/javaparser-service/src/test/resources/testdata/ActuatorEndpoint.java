package testdata;

import org.springframework.boot.actuate.endpoint.annotation.*;

@Endpoint(id = "custom")
public class ActuatorEndpoint {

    @ReadOperation
    public String read() { return "read"; }

    @WriteOperation
    public String write() { return "written"; }

    @DeleteOperation
    public String delete() { return "deleted"; }
}
