package testdata;

import org.springframework.messaging.rsocket.RSocketRequester;
import org.springframework.messaging.handler.annotation.*;

@Controller
public class RSocketController {

    @MessageMapping("rsocket.route")
    public String handleRSocket(RSocketRequester requester) { return "rsocket"; }
}
