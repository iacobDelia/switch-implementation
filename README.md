# Switch implementation

## MAC and VLAN
First, the switch checks the MAC to see if it has received a BPDU frame or not. If not, then it redirects it to the next destionation.
After it decides the interface it wants to send it to, it calls the funcion that handles the VLAN stage. If it sends it on the trunk then it attaches the tag, but if the interface is access then the router will remove it.

## BPDU implementation
The switch initializes all interfaces as DESIGNATED, and once it receives enough information it blocks the right paths as to not create loops in the network.
Meanwhile, the root switch continuously sends frames to the rest of the switches to check their status.
