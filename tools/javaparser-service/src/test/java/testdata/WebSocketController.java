package testdata;

import org.springframework.messaging.handler.annotation.*;

@Controller
public class WebSocketController {

    @MessageMapping("/ws")
    public String handleMessage() { return "msg"; }

    @SubscribeMapping("/topic")
    public String handleSubscribe() { return "sub"; }
}
