import { LoginServerConnection } from 'ta-network-api';
import fs from 'fs';
import ini from 'ini';

// Define the custom login server information
const server = {
	name: 'PUGs',
	ip: 'Ta.dodgesdomain.com',
    // ip: 'ta.kfk4ever.com',
    port: 9000,
	isLoginServer: true,
	supportsGOTY: true,
	supportsOOTB: true,
	isSecure: true
};

const userconfig = ini.parse(fs.readFileSync('./config.ini', 'utf-8'));


// Your account credentials for the login server
const credentials = {
	username: userconfig.API.Username,
	passwordHash: userconfig.API.PasswordHash,
	salt: new Uint8Array()
};

// Optional configuration for the login server connection
const config = {
	authenticate: true,
	debug: false,
	timeout: 150,
	buffer: {
		debug: false
	},
	decoder: {
		clean: true,
		debug: false
	}
};

// Create a new connection instance
const connection = new LoginServerConnection(server, credentials, config);

async function fetchOnlinePlayerData(serverId) {
    // Initiate the connection with the server
    await connection.connect();

    // Fetch the number of online players
    const onlinePlayerNumber = await connection.fetch('OnlinePlayerNumber');
    // console.log('Number of players online:', onlinePlayerNumber);

    // Fetch the list of online players
    const onlinePlayerList = await connection.fetch('OnlinePlayerList');
    // console.log('List of players online:', onlinePlayerList);

    // Fetch the list of game servers
    const gameServerList = await connection.fetch('GameServerList');
    // console.log('List of game servers:', gameServerList);

    const specificServerInfo = await connection.fetch('GameServerInfo', serverId);
    console.log('Information about the specific server:', specificServerInfo);
}

// Run the function
//fetchOnlinePlayerData(3).catch(error => console.error('An error occurred:', error));

async function fetchGameServerList() {
    try {
        console.error('Total Execution Time: Start');
        const startTotalTime = Date.now();
        
        console.error('Connect Time: Start');
        const startConnectTime = Date.now();
        await connection.connect();
        console.error('Connect Time: End', Date.now() - startConnectTime);
        
        console.error('Fetch GameServerList Time: Start');
        const startFetchListTime = Date.now();
        let gameServerList = await connection.fetch('GameServerList');
        console.error('Fetch GameServerList Time: End', Date.now() - startFetchListTime);

        // Concurrently fetch specific server info
        console.error('Fetch Specific Server Info Time: Start');
        const startFetchInfoTime = Date.now();
        const promises = gameServerList.map(server => {
            return (async () => {
                if (server.numberOfPlayers > 0) {
                    const specificServerInfo = await connection.fetch('GameServerInfo', server.id);
                    server.specificServerInfo = specificServerInfo;
                }
                return server;
            })();
        });
        const resolvedServers = await Promise.all(promises);
        console.error('Fetch Specific Server Info Time: End', Date.now() - startFetchInfoTime);

        console.log(JSON.stringify(resolvedServers));
        
        console.error('Total Execution Time: End', Date.now() - startTotalTime);
    } catch (error) {
        console.error('An error occurred:', error);
    }
}

// Run the function
fetchGameServerList();


