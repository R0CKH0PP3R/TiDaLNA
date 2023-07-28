# TiDaLNA
A Tidal UPnP/DLNA client 

This is a working proof of concept. I often use Plexamp and UPnP/DLNA compatibility is frequently requested on their forum. The Plexamp devs think DLNA sucks and would prefer to go their own way, which is totally understandable. However, a demand remains & this is intended to show that it's not that hard and that it doesn't have to suck. Yes, every UPnP/DLNA library I tried sucked hard, but it was easy to reverse engineer the required soap requests with Wireshark from BubbleUPnP, so that those libraries were not required.

Being a proof of concept, this is rather limited. Firstly, I am not a developer, rather an enthusiast looking to see what's possible. Secondly, this is currently limited to playing albums, but playlists and tracks should comprise trivial modifications. Finally, and perhaps most importantly, there is no error handling and testing has been limited to my setup which assumes a local device with upmpdcli configured to use mpd as the renderer, so YMMV.

My hope is that this is seen by someone who has the proper skills to take this forward. User interfaces are not of interest to me and I realise that's needed to go much further. 

Did I mention that this already provides gapless playback? No? Well, please take a look.
