package testdata;

import org.springframework.graphql.data.method.annotation.*;

@Controller
public class GraphQLController {

    @QueryMapping
    public String hello() { return "hello"; }

    @MutationMapping
    public String createItem() { return "created"; }

    @SubscriptionMapping
    public String subscribe() { return "sub"; }
}
