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


async function fetchGameServerList() {
    try {
        let gameServerList = await connection.fetch('GameServerList');

        // Filter out servers without a valid ID
        const validServers = gameServerList.filter(server => server.id !== undefined && server.id !== null);

        // Concurrently fetch detailed server info using the valid server IDs
        const promises = validServers.map(server => {
            return connection.fetch('GameServerInfo', server.id);
        });
        const detailedServers = await Promise.all(promises);

        console.log(JSON.stringify(detailedServers));
    
    } catch (error) {
        console.error('An error occurred:', error);
    }
}

fetchGameServerList();


