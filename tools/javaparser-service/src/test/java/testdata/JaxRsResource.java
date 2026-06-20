package testdata;

import javax.ws.rs.*;

@Path("/api")
public class JaxRsResource {

    @GET
    public String getUsers() { return "users"; }

    @POST
    public String create() { return "created"; }

    @PUT
    @Path("/sub")
    public String update() { return "updated"; }

    @DELETE
    public String delete() { return "deleted"; }

    @PATCH
    @Path("/partial")
    public String patch() { return "patched"; }
}
